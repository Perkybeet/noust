defmodule Hello.Application do
  @moduledoc false
  use Application

  @impl true
  def start(_type, _args) do
    port = String.to_integer(System.get_env("PORT") || "4000")
    {:ok, ip} = :inet.parse_address(String.to_charlist(System.get_env("HOST") || "127.0.0.1"))

    children = [
      {Plug.Cowboy, scheme: :http, plug: Hello.Router, options: [ip: ip, port: port]}
    ]

    Supervisor.start_link(children, strategy: :one_for_one, name: Hello.Supervisor)
  end
end
