defmodule DbPortal.MixProject do
  use Mix.Project

  def project do
    [
      app: :db_portal,
      version: "0.1.0",
      elixir: "~> 1.7",
      elixirc_paths: elixirc_paths(Mix.env()),
      start_permanent: Mix.env() == :prod,
      aliases: aliases(),
      deps: deps()
    ]
  end

  # Configuration for the OTP application.
  #
  # Type `mix help compile.app` for more information.
  def application do
    [
      mod: {DbPortal.Application, []},
      extra_applications: [:logger, :runtime_tools, :porcelain]
    ]
  end

  # Specifies which paths to compile per environment.
  defp elixirc_paths(:test), do: ["lib", "test/support"]
  defp elixirc_paths(_), do: ["lib"]

  # Specifies your project dependencies.
  #
  # Type `mix help deps` for examples and options.
  defp deps do
    [
      {:phoenix, "~> 1.7.18"},
      {:phoenix_view, "~> 2.0"},
      {:phoenix_ecto, "~> 4.5"},
      {:bandit, ">= 0.6.9"},
      {:ecto_sql, "~> 3.8"},
      {:myxql, ">= 0.0.0"},
      {:phoenix_live_view, "~> 1.0.1"},
      {:floki, ">= 0.0.0", only: :test},
      {:phoenix_html, "~> 4.1"},
      {:phoenix_html_helpers, "~> 1.0.0"},
      {:phoenix_live_reload, "~> 1.4", only: :dev},
      {:phoenix_live_dashboard, "~> 0.8"},
      {:telemetry_metrics, "~> 0.6"},
      {:telemetry_poller, "~> 0.5"},
      {:gettext, "~> 0.11"},
      {:jason, "~> 1.0"},
      {:esbuild, "~> 0.7.0", runtime: Mix.env() == :dev},
      {:credo, "~> 1.7", only: [:dev, :test], runtime: false},
      {:tailwind, "~> 0.1"},
      {:kaffy, "~> 0.10.0"},
      {:pathex, "~> 2.1.0"},
      {:con_cache, "~> 0.14.0"},
      # hex pkg fails on elixir 1.15 because of 'deps' fxn call w/o parens.  This is a fork
      {:recon_ex, git: "https://github.com/liberlanco/recon_ex.git", ref: "master"},
      {:finch, "~> 0.2"},
      {:chartkick, "~> 0.4"},
      {:nimble_csv, "~> 1.1.0"},
      {:slack, "~> 0.23.5"},
      {:site_encrypt, "~> 0.6.0"},
      # would like to get rid of Cowboy altogether.  But, esaml (dependency of samly) needs it
      # problem is cowlib won't compile on certain based (in Docker eg: hexpm/elixir:1.17.2-erlang-27.0.1-alpine-3.20.2
      {:cowboy, "~> 2.8.0", override: true},
      {:cowlib, "2.8.0", override: true},
      {:plug_cowboy, "~> 2.0"},
      {:samly, git: "https://github.com/rliebling/samly"},
      {:ex_aws, git: "https://github.com/rliebling/ex_aws", ref: "b520d1d5", override: true},
      {:ex_aws_secretsmanager, "~> 2.0"},
      {:ex_aws_rds, "~> 2.0"},
      {:configparser_ex, "~> 5.0"},
      {:sweet_xml, "~> 0.6.6", override: true},
      {:porcelain, "~> 2.0"},
      {:tzdata, "~> 1.1"},
      {:vapor, "~> 0.10.0"},
      {:aws,
       git: "https://github.com/aws-beam/aws-elixir",
       ref: "debdf6482158a71a57636ac664c911e682093395"},
      {:live_monaco_editor, "~> 0.2"}
    ]
  end

  # Aliases are shortcuts or tasks specific to the current project.
  # For example, to install project dependencies and perform other setup tasks, run:
  #
  #     $ mix setup
  #
  # See the documentation for `Mix` for more info on aliases.
  defp aliases do
    [
      setup: ["deps.get", "ecto.setup"],
      "ecto.setup": ["ecto.create", "ecto.migrate", "run priv/repo/seeds.exs"],
      "ecto.reset": ["ecto.drop", "ecto.setup"],
      test: ["ecto.create --quiet", "ecto.migrate --quiet", "test"],
      "assets.deploy": [
        "tailwind default --minify",
        "esbuild default --minify",
        "phx.digest"
      ]
    ]
  end
end
