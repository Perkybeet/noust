using Newtonsoft.Json;

var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

// The content root is the publish directory: wwwroot/version.txt sits beside the program.
var version = File.ReadAllText(Path.Combine(app.Environment.ContentRootPath, "wwwroot", "version.txt")).Trim();

app.MapGet("/", () => $"dotnet-web {version}\n");
app.MapGet("/json", () => JsonConvert.SerializeObject(new { app = "dotnet-web", version }));

app.Run();
