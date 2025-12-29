defmodule DbPortalWeb.Endpoint do
  use SiteEncrypt.Phoenix.Endpoint, otp_app: :db_portal

  # The session will be stored in the cookie and signed,
  # this means its contents can be read but not tampered with.
  # Set :encryption_salt if you would also like to encrypt it.
  @session_options [
    store: :cookie,
    key: "_db_portal_key",
    signing_salt: "hnU/EjYk"
  ]

  socket "/socket", DbPortalWeb.UserSocket,
    websocket: true,
    longpoll: false

  socket "/live", Phoenix.LiveView.Socket, websocket: [connect_info: [session: @session_options]]

  # Serve at "/" the static files from "priv/static" directory.
  #
  # You should set gzip to true if you are running phx.digest
  # when deploying your static files in production.
  plug Plug.Static,
    at: "/",
    from: :db_portal,
    gzip: false,
    only: DbPortalWeb.static_paths()

  plug Plug.Static,
    # or "/path/to/your/static/kaffy"
    at: "/kaffy",
    from: :kaffy,
    gzip: false,
    only: ~w(assets)

  # Code reloading can be explicitly enabled under the
  # :code_reloader configuration of your endpoint.
  if code_reloading? do
    socket "/phoenix/live_reload/socket", Phoenix.LiveReloader.Socket
    plug Phoenix.LiveReloader
    plug Phoenix.CodeReloader
    plug Phoenix.Ecto.CheckRepoStatus, otp_app: :db_portal
  end

  plug Phoenix.LiveDashboard.RequestLogger,
    param_key: "request_logger",
    cookie_key: "request_logger"

  plug Plug.RequestId
  plug Plug.Telemetry, event_prefix: [:phoenix, :endpoint]

  plug Plug.Parsers,
    parsers: [:urlencoded, :multipart, :json],
    pass: ["*/*"],
    json_decoder: Phoenix.json_library()

  plug Plug.MethodOverride
  plug Plug.Head
  plug Plug.Session, @session_options
  plug DbPortalWeb.Router

  @impl SiteEncrypt
  def certification do
    mode = if System.get_env("SSLCERT_MODE") == "manual", do: :manual, else: :auto

    dir_url =
      case System.get_env("LE_MODE", "local") do
        "local" -> {:internal, port: 4102}
        "staging" -> "https://acme-staging-v02.api.letsencrypt.org/directory"
        "production" -> "https://acme-v02.api.letsencrypt.org/directory"
      end

    domain =
      Application.get_env(:db_portal, DbPortalWeb.Endpoint)
      |> get_in([:url, :host])

    SiteEncrypt.configure(
      # Note that native client is very immature. If you want a more stable behaviour, you can
      # provide `:certbot` instead. Note that in this case certbot needs to be installed on the
      # host machine.
      client: :native,
      domains: [domain],
      emails: ["rich.liebling@coursehero.com"],
      db_folder: Application.app_dir(:db_portal, Path.join(~w/priv site_encrypt/)),
      mode: mode,
      # set OS env var MODE to "staging" or "production" on staging/production hosts
      directory_url: dir_url
    )
  end

  @impl Phoenix.Endpoint
end
