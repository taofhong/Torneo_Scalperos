#region Using declarations
using System;
using System.Net.Http;
using System.Text;
using NinjaTrader.Cbi;
using NinjaTrader.NinjaScript;
#endregion


namespace NinjaTrader.NinjaScript.Strategies
{
public class TorneoPost : Strategy
{
private static readonly HttpClient client = new HttpClient();


protected override void OnExecutionUpdate(Cbi.Execution execution, string executionId, double price, int quantity, Cbi.MarketPosition marketPosition, string orderId, DateTime time)
{
// Solo continuar si la orden existe y ha sido rellenada (Filled)
if (execution?.Order == null || execution.Order.OrderState != OrderState.Filled)
return;


try
{
var payload = new
{
handle = Account?.Name ?? "trader",
symbol = execution.Order.Instrument?.FullName ?? "UNKNOWN",
qty = quantity,
price = price,
position_size = execution.Order.Quantity,
realized_pnl = Account?.Get(AccountItem.RealizedProfitLoss, Currency.UsDollar) ?? 0.0,
ts = (long)(DateTimeOffset.UtcNow.ToUnixTimeMilliseconds())
};


var json = System.Text.Json.JsonSerializer.Serialize(payload);
var content = new StringContent(json, Encoding.UTF8, "application/json");


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