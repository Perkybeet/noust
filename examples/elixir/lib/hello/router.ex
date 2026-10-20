defmodule Hello.Router do
  use Plug.Router

  plug :match
  plug :dispatch

  get "/" do
    send_resp(conn, 200, "Hello from Noust (elixir)\n")
  end

  match _ do
    send_resp(conn, 404, "not found\n")
  end
end
