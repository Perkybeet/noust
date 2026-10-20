// An ASP.NET Core application. Noust publishes it with the server's SDK and runs the result;
// ASPNETCORE_URLS is set to the loopback address and the port it chose.
var app = WebApplication.CreateBuilder(args).Build();

app.MapGet("/", () => "Hello from Noust (dotnet)\n");

app.Run();
