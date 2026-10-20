defmodule Hello.MixProject do
  use Mix.Project

  def project do
    [
      app: :hello,
      version: "1.0.0",
      elixir: "~> 1.14",
      start_permanent: Mix.env() == :prod,
      deps: deps(),
      releases: [hello: [include_executables_for: [:unix]]]
    ]
  end

  def application do
    [extra_applications: [:logger], mod: {Hello.Application, []}]
  end

  # Pinned to what Elixir 1.14 and Erlang/OTP 25 (Ubuntu 24.04's packages) can compile: a current plug
  # wants Elixir 1.15 and a current cowlib wants OTP 26.
  defp deps do
    [
      {:plug_cowboy, "~> 2.6.2"},
      {:plug, "~> 1.14.2"},
      {:cowboy, "== 2.10.0"},
      {:cowlib, "== 2.12.1"}
    ]
  end
end
