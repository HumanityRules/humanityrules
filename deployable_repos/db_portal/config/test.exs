import Config

# Configure your database
#
# The MIX_TEST_PARTITION environment variable can be used
# to provide built-in test partitioning in CI environment.
# Run `mix help test` for more information.
config :db_portal, DbPortal.Repo,
  username: "root",
  password: "",
  database: "db_portal_test#{System.get_env("MIX_TEST_PARTITION")}",
  hostname: "localhost",
  pool: Ecto.Adapters.SQL.Sandbox

# We don't run a server during test. If one is required,
# you can enable the server option below.
config :db_portal, DbPortalWeb.Endpoint,
  http: [port: 4002],
  url: [host: "dataengr.local", port: 15000],
  server: false

# Print only warnings and errors during test
config :logger, level: :debug

config :db_portal, :env, :test

# disable retrieving secrets from AWS Secrets Manager
config :db_portal, :no_secrets_mgr, slack_token: "foobar", signing_salt: "gFTHjOPy7N7OK8V0OhcJcYfQKLecQlb9", secret_key_base: "J6lmV9W2YCjXj7MyKeYvl++KkgPqahm+ZZvOg2V2SKhEGyrKOI3vl2UQUox0PAWa"
