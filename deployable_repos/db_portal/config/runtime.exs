import Config

defmodule ConfigHelper do
  @moduledoc ~S"""
  want the path to "priv/foo/bar" evaluated at runtime, as the location of
  `priv` dir depends on whether we run from a release or not. when
  running in `mix phx.server` there is a `priv/` dir directly in the current dir,
  But, in a release it is relative to the application directory.  And, even when
  run from mix, it should not be accessed from the source directory but from inside
  the build.
  """

  def priv_path(path) do
    Application.app_dir(:db_portal)
    |> to_string()
    |> Path.join("priv")
    |> Path.join(path)
  end
end

{entity_id, metadata_file, org_url, base_url} = case Application.get_env(:db_portal, :env) do
  :dev -> {"samly_entity_id", ConfigHelper.priv_path("okta/okta_metadata.xml"),  "http://dataengr.local:15000/",  "http://dataengr.local:15000/sso"}

  :prod  -> { "5e584789691b8a0c95c417d9", ConfigHelper.priv_path("okta/ch.okta_metadata.xml"),  "https://dataengr.coursehero.io:4343", "https://dataengr.coursehero.io/sso"}

  :test -> {"samly_entity_id", ConfigHelper.priv_path("okta/okta_metadata.xml"),  "http://dataengr.local:15000/",  "http://dataengr.local:15000/sso"}
end

# DISABLE_AUTH=true bypasses Okta authentication (for running behind ALB without SSO)
use_okta_auth = cond do
  System.get_env("DISABLE_AUTH") == "true" -> false
  Application.get_env(:db_portal, :env) == :prod -> true
  System.get_env("MY_OKTA") != nil -> true
  true -> false
end
config :db_portal, :use_okta_auth, use_okta_auth

config :samly, Samly.State,
  store: Samly.State.ETS,
  opts: [key: "my_samly_state_session_key"]

config :samly, Samly.Provider,
  idp_id_from: :path_segment,
  service_providers: [
    %{
      id: "sp1",
      #entity_id: "urn:samly.howto:samly_sp",
      #entity_id: "samly_entity_id",
      #entity_id: "5e584789691b8a0c95c417d9",
      entity_id: entity_id,
      certfile: ConfigHelper.priv_path("cert/samly_sp.pem"),
      keyfile: ConfigHelper.priv_path("cert/samly_sp_key.pem"),
      contact_name: "Samly Howto SP1 Admin",
      contact_email: "sp1-admin@samly.howto",
      org_name: "Samly Howto SP1",
      org_displayname: "Samly Howto SP1 Displayname",
      org_url: org_url
    }
  ],
  identity_providers: [
    %{
      id: "okta",
      sp_id: "sp1",
      base_url: base_url,
      #metadata_file: "config/okta_metadata.xml",
      #metadata_file: "config/ch.okta_metadata.xml",
      metadata_file: metadata_file,
      pre_session_create_pipeline: DbPortalWeb.Plugs.SamlyPipeline,
      allow_idp_initiated_flow: true,
      allowed_target_urls: nil,
      use_redirect_for_req: false,
      sign_requests: true,
      sign_metadata: true,
      signed_assertion_in_resp: true,
      signed_envelopes_in_resp: true,
      nameid_format: :transient
    }
  ]

config :db_portal, :sampler, System.get_env("RUN_SAMPLER")

default_aws_profile = System.get_env("AWS_DEFAULT_PROFILE", "default")
config :ex_aws,
  http_client: ExAwsFinchAdapter,
  finch_process_name: ExAwsFinch,
  access_key_id: [{:system, "AWS_ACCESS_KEY_ID"}, {:awscli,default_aws_profile, 30}, :instance_role],
  secret_access_key: [{:system, "AWS_SECRET_ACCESS_KEY"}, {:awscli, default_aws_profile, 30}, :instance_role]

# Database configuration - supports either DATABASE_URL or individual components
# Individual components are used when ECS injects secrets (DATABASE_PASSWORD, DATABASE_USERNAME)
cond do
  # Option 1: Full DATABASE_URL provided
  database_url = System.get_env("DATABASE_URL") ->
    config :db_portal, DbPortal.Repo,
      url: database_url,
      pool_size: String.to_integer(System.get_env("POOL_SIZE", "10"))

  # Option 2: Individual components (from ECS secrets injection)
  System.get_env("DATABASE_HOST") ->
    config :db_portal, DbPortal.Repo,
      hostname: System.get_env("DATABASE_HOST"),
      port: String.to_integer(System.get_env("DATABASE_PORT", "3306")),
      database: System.get_env("DATABASE_NAME", "db_portal_prod"),
      username: System.get_env("DATABASE_USERNAME", "dbadmin"),
      password: System.get_env("DATABASE_PASSWORD", ""),
      pool_size: String.to_integer(System.get_env("POOL_SIZE", "10"))

  # Option 3: No external database configured (use defaults from dev.exs/prod.exs)
  true ->
    :ok
end

# HTTP-only endpoint configuration for running behind ALB (TLS terminated at load balancer)
if System.get_env("DISABLE_HTTPS") == "true" do
  http_port = String.to_integer(System.get_env("PORT", "4000"))
  host = System.get_env("PHX_HOST", "localhost")

  config :db_portal, DbPortalWeb.Endpoint,
    url: [host: host, port: 443],  # External URL uses 443 (ALB terminates TLS)
    http: [port: http_port],        # Internal port for ALB health checks
    https: false,                   # Disable HTTPS - ALB handles TLS
    server: true,
    check_origin: false
end

