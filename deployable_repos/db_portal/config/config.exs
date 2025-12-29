# This file is responsible for configuring your application
# and its dependencies with the aid of the Mix.Config module.
#
# This configuration file is loaded before any dependency and
# is restricted to this project.

# General application configuration
import Config

config :elixir, :time_zone_database, Tzdata.TimeZoneDatabase

config :db_portal,
  ecto_repos: [DbPortal.Repo]

# Configures the endpoint
config :db_portal, DbPortalWeb.Endpoint,
  #url - set in environment specific file
  secret_key_base: "TO_BE_FETCHED",
  render_errors: [view: DbPortalWeb.ErrorView, accepts: ~w(html json), layout: false],
  pubsub_server: DbPortal.PubSub,
  live_view: [signing_salt: "TO_BE_FETCHED"],
  https: [port: 4343],
  http: [port: 43430]

# Configures Elixir's Logger
config :logger, :console,
  format: "$date $time $metadata[$level] $message\n",
  metadata: [:request_id]

config :logger,
  backends: [:console, DbPortal.LogNotifier]

config :logger, :log_notifier,
  format: "$date $time $metadata[$level] $message\n",
  slack_channel: "G0173V62KL5", #private zzz_test
  metadata: [:id],
  env: Mix.env()

# Use Jason for JSON parsing in Phoenix
config :phoenix, :json_library, Jason

config :tailwind,
  version: "3.0.12",
  default: [
    args: ~w(
      --config=tailwind.config.js
      --input=css/app.scss
      --output=../priv/static/assets/app.css
    ),
    cd: Path.expand("../assets", __DIR__)
  ]

config :esbuild,
  version: "0.13.4",
  default: [
    args: ~w(js/app.js --bundle --target=es2016 --outdir=../priv/static/assets),
    cd: Path.expand("../assets", __DIR__),
    env: %{"NODE_PATH" => Path.expand("../deps", __DIR__)}
  ]

# Import environment specific config. This must remain at the bottom
# of this file so it overrides the configuration defined above.
import_config "#{Mix.env()}.exs"

config :kaffy,
  otp_app: :db_portal,
  ecto_repo: DbPortal.Repo,
  router: DbPortalWeb.Router,
  resources: [
      permissions: [
        name: "Permissions", # a custom name for this context/section.
        resources: [ # this line used to be "schemas" in pre v0.9
          permission: [schema: DbPortal.Permission, admin: DbPortal.PermissionAdmin],
        ]
      ],
      clusters: [
        name: "Clusters",
        resources: [
          clusters: [schema: DbPortal.DbMetadata.Cluster],
        ]
      ],
      break_glass_users: [
        name: "BreakGlass Authorized Users",
        resources: [
          authorized_user: [schema: DbPortal.BreakGlass.AuthorizedUser, admin: DbPortal.BreakGlass.AuthorizedUserAdmin]
        ]
      ]
    ]

config :db_portal, DbPortalWeb.Endpoint,
  adapter: Bandit.PhoenixAdapter
