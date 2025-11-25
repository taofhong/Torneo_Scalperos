#region Using declarations
using System;
using System.ComponentModel; // Necesario para [Display]
using System.Net.Http;
using System.Text;
using System.Security.Cryptography; // Para HMAC
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
#endregion


namespace NinjaTrader.NinjaScript.Strategies
{
    public class TorneoPost : Strategy
    {
        private static readonly HttpClient client = new HttpClient();
        
        // =======================================================
        // PROPIEDADES (CONFIGURACIÓN) - ¡AHORA EDITABLES EN NT8!
        // =======================================================
        
        [NinjaScriptProperty]
        [Display(Name="1. Handle de Trader", Order=1, GroupName="Torneo Scalperos")]
        public string TraderHandle { get; set; } = "Pega_Aqui_Tu_Handle";

        [NinjaScriptProperty]
        [Display(Name="2. API Key", Order=2, GroupName="Torneo Scalperos")]
        public string ApiKey { get; set; } = "";

        [NinjaScriptProperty]
        [Display(Name="3. Secret Key", Order=3, GroupName="Torneo Scalperos")]
        public string SecretKey { get; set; } = "";
        
        // =======================================================
        
        // Mantiene el PnL REALIZADO acumulado de la cuenta (para enviarlo como una métrica)
        private double lastRealizedPnl = 0.0;
        
        // Método para calcular la firma HMAC
        private string CalculateHmac(string timestamp, string body)
        {
            if (string.IsNullOrEmpty(SecretKey)) return string.Empty;

            // Formato de mensaje requerido por FastAPI: timestamp.body
            string message = $"{timestamp}.{body}";
            byte[] keyBytes = Encoding.UTF8.GetBytes(SecretKey);
            byte[] messageBytes = Encoding.UTF8.GetBytes(message);

            using (var hmacsha256 = new HMACSHA256(keyBytes))
            {
                byte[] hashBytes = hmacsha256.ComputeHash(messageBytes);
                // Convertir el hash a formato hexadecimal
                return BitConverter.ToString(hashBytes).Replace("-", "").ToLowerInvariant();
            }
        }

        protected override void OnExecutionUpdate(Cbi.Execution execution, string executionId, double price, int quantity, Cbi.MarketPosition marketPosition, string orderId, DateTime time)
        {
            // Solo continuar si la orden existe y ha sido rellenada (Filled)
            if (execution?.Order == null || execution.Order.OrderState != OrderState.Filled)
                return;
            
            // Calculamos el PnL realizado desde la última actualización
            double currentRealizedPnl = Account?.Get(AccountItem.RealizedProfitLoss, Currency.UsDollar) ?? 0.0;
            
            // IMPORTANTE: Este valor es el cambio de PnL desde la última ejecución, que el servidor sumará.
            double realizedPnlChange = currentRealizedPnl - lastRealizedPnl; 
            
            // Actualizamos el acumulado para el siguiente cálculo
            lastRealizedPnl = currentRealizedPnl;

            // Si el cambio es cero (e.g., ajuste de cuenta), podemos ignorarlo
            if (realizedPnlChange == 0 && execution.Order.Quantity == 0) return;
            
            try
            {
                long tsMillis = (long)(DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());
                string tsString = tsMillis.ToString();
                
                var payload = new
                {
                    handle = TraderHandle, // Usa la propiedad configurable
                    symbol = execution.Order.Instrument?.FullName ?? "UNKNOWN",
                    qty = quantity,
                    price = price,
                    position_size = execution.Order.Quantity,
                    realized_pnl = realizedPnlChange, // Enviamos el PnL REALIZADO de este trade
                    ts = tsMillis
                };


                string json = System.Text.Json.JsonSerializer.Serialize(payload);
                StringContent content = new StringContent(json, Encoding.UTF8, "application/json");
                
                // Preparar encabezados de seguridad
                string signature = CalculateHmac(tsString, json);

                client.DefaultRequestHeaders.Clear();
                client.DefaultRequestHeaders.Add("X-API-Key", ApiKey);
                client.DefaultRequestHeaders.Add("X-Timestamp", tsString);
                client.DefaultRequestHeaders.Add("X-Signature", signature);

                // Enviar al servidor de FastAPI
                var resp = client.PostAsync("http://127.0.0.1:8000/events/trade", content).Result;
                Print($"POST /events/trade -> {resp.StatusCode}");
            }
            catch (Exception ex)
            {
                Print($"Error POST trade: {ex.Message}");
            }
        }
    }
}