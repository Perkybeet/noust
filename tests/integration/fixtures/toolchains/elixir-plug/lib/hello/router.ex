defmodule Hello.Router do
  use Plug.Router

  plug :match
  plug :dispatch

  @version "v1"

  get "/" do
    send_resp(conn, 200, "elixir-plug #{@version}\n")
  end

  match _ do
    send_resp(conn, 404, "not found\n")
  end
end
