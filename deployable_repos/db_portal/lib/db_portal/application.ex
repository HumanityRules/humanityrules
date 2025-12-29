defmodule DbPortal.Application do
  # See https://hexdocs.pm/elixir/Application.html
  # for more information on OTP Applications
  @moduledoc false

  use Application

  require Logger

  alias DbPortal.SecretsManagerConfigProvider

  def start(_type, _args) do
    finch_name = Application.get_env(:ex_aws, :finch_process_name)

    conf = configuration(finch_name)
    orig = Application.get_env(:db_portal, DbPortalWeb.Endpoint)

    new =
      Keyword.merge(orig,
        live_view: [signing_salt: conf["signing_salt"]],
        secret_key_base: conf["secret_key_base"]
      )

    Application.put_env(:db_portal, DbPortalWeb.Endpoint, new, persistent: true)
    Application.put_env(:slack, :api_token, conf[:slack_token])

    run_sampler =
      if "Y" == Application.get_env(:db_portal, :sampler),
        do: [DbPortal.SamplerScheduler],
        else: []

    IO.inspect(run_sampler, label: "############### RUN SAMPLER?")

    children =
      [
        # Start the Ecto repository
        DbPortal.Repo,
        # Start the Telemetry supervisor
        DbPortalWeb.Telemetry,
        # Start the PubSub system
        {Phoenix.PubSub, name: DbPortal.PubSub},
        # Start the Endpoint (http/https)
        DbPortalWeb.Endpoint,
        DbPortal.ConCacheSupervisor,
        {Samly.Provider, []},
        {Finch, name: finch_name},
        {DbPortal.Secrets, []},
        DbPortal.Ptosc,
        DbPortal.AthenaTools.AthenaJobSupervisor,
        {Registry, keys: :unique, name: Registry.Ptosc},
        {Registry, keys: :unique, name: Registry.AthenaAsyncOp},
        {Registry, keys: :unique, name: Registry.DynamicSampler},
        DbPortal.BreakGlass.AccountScrubber,
        DbPortal.DynamicSampling.SamplerSupervisor
      ] ++ run_sampler

    # See https://hexdocs.pm/elixir/Supervisor.html
    # for other strategies and supported options
    opts = [strategy: :one_for_one, name: DbPortal.Supervisor]

    r =
      case Supervisor.start_link(children, opts) do
        {:error,
         {:shutdown,
          {:failed_to_start_child, SiteEncrypt.Phoenix,
           {{:badmatch,
             {:error,
              {:shutdown,
               {:failed_to_start_child, {:ranch_listener_sup, DbPortalWeb.Endpoint.HTTP},
                {:shutdown,
                 {:failed_to_start_child, :ranch_acceptors_sup,
                  {:listen_error, DbPortalWeb.Endpoint.HTTP, :eaddrinuse}}}}}}},
            _stacktrace}}}} = e ->
          endpoint_conf = Application.get_env(:db_portal, DbPortalWeb.Endpoint)
          endpoint_ports = Enum.flat_map([:http, :https], &[Keyword.get(endpoint_conf, &1, nil)])

          Logger.error(
            "EADDRINUSE: cannot listen on at least one of these ports #{inspect(endpoint_ports)}. *** You must have another application listening on one of those ports.  In macos, can try `lsof -i -P | grep <PUT_PORT_HERE> to see. ***"
          )

          e

        rr ->
          rr
      end

    # Must initialize!
    DbPortal.Secrets.init(conf["db_password"])
    r
  end

  defp configuration(finch_name) do
    case Application.get_env(:db_portal, :no_secrets_mgr) do
      nil ->
        providers = [
          # NOTE: these secrets must be tagged with application=db_portal for the IAM policy to grant it read access
          %SecretsManagerConfigProvider{
            key: "dbportal_k",
            value: "dbportal_secret",
            slack_token: "dbportal_slack",
            finch_name: finch_name
          }
        ]

        Vapor.load!(providers)

      conf ->
        %{slack_token: slack_token, signing_salt: signing_salt, secret_key_base: secret_key_base} =
          Map.new(conf)

        %{
          :slack_token => slack_token,
          "signing_salt" => signing_salt,
          "secret_key_base" => secret_key_base
        }
    end
  end

  # Tell Phoenix to update the endpoint configuration
  # whenever the application is updated.
  def config_change(changed, _new, removed) do
    DbPortalWeb.Endpoint.config_change(changed, removed)
    :ok
  end
end
