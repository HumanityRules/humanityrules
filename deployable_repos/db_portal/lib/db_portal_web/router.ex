defmodule DbPortalWeb.Router do
  use DbPortalWeb, :router

  use Kaffy.Routes, scope: "/kadmin", pipe_through: [:browser, :auth, :data_engr_auth]
  require Logger

  alias Plug.Conn
  alias DbPortalWeb.ErrorView

  pipeline :browser do
    plug :accepts, ["html"]
    plug :fetch_session
    plug :fetch_live_flash
    plug :put_root_layout, {DbPortalWeb.LayoutView, :root}
    plug :protect_from_forgery
    plug :put_secure_browser_headers
  end

  pipeline :auth do
    plug :auth_verify
  end

  pipeline :data_engr_auth do
    plug :data_engr_auth_verify
  end

  defp redirect_login(conn) do
    return_to = %{return: %{path: conn.request_path, qry: conn.query_string}}

    conn
    |> redirect(to: ~p"/login?#{return_to}")
    |> Conn.halt()
  end

  def delete_assertion(conn) do
    assertion_key = Conn.get_session(conn, "samly_assertion_key")
    conn = if !is_nil(assertion_key) do
      Samly.State.delete_assertion(conn, assertion_key)
    else
      conn
    end

    conn = Samly.State.delete_assertion(conn, assertion_key)

    IO.inspect(Samly.State.get_assertion(conn, assertion_key),
      label: "delete assertion in pipeline"
    )

    conn
  end

  def do_fake_verify(conn, _opts) do
    case Conn.get_session(conn, :user_auth) do
      {email, valid_until, _} ->
        if DateTime.compare(DateTime.utc_now(), valid_until) == :gt do
          IO.puts("Expired login session #{valid_until}")
          future = DateTime.utc_now() |> DateTime.add(30*24*3600)
          conn |> Conn.put_session(:user_auth, {email, future, nil})
        else
          conn
        end

      _ ->
        email = "dev@example.com"

        future = DateTime.utc_now() |> DateTime.add(30*24*3600)

        conn
        |> Conn.assign(:email, email)
        |> Conn.assign(:avatar, DbPortalWeb.Avatar.get(email))
        |> Conn.put_session(:user_auth, {email, future, nil})
    end
  end

  def data_engr_auth_verify(conn, _opts) do
    user_auth = {email, _valid_until, _role} = Conn.get_session(conn, :user_auth)

    case DbPortalWeb.UserAuthLive.check_page_permission(Map.get(conn, :request_path), email, user_auth) do
      {:ok, _} ->
        conn

      _ ->
        put_status(conn, 403)
        |> put_view(ErrorView)
        |> render("403.html", %{message: "Only members of Data Engineering may access this page."})
        |> Conn.halt()
    end
  end

  def auth_verify(conn, opts) do
    if Application.get_env(:db_portal, :use_okta_auth) do
      do_okta_verify(conn, opts)
    else
      do_fake_verify(conn, opts)
    end
  end

  def do_okta_verify(conn, _opts) do
    case Conn.get_session(conn, :user_auth) do
      {email, valid_until, _} ->
        if DateTime.compare(DateTime.utc_now(), valid_until) == :gt do
          IO.puts("Expired login session #{valid_until}")

          conn
          |> delete_assertion
          |> Conn.assign(:email, email)
          |> Conn.assign(:avatar, DbPortalWeb.Avatar.get(email))
          |> redirect_login()
        else
          Conn.get_session(conn, "samly_assertion_key")
          |> IO.inspect(label: "auth_verify samly_assertion_key")

          Logger.info("logging in user #{email}")
          conn
          |> Conn.assign(:email, email)
          |> Conn.assign(:avatar, DbPortalWeb.Avatar.get(email))
        end

      _ ->
        IO.puts("No login session")
        redirect_login(conn)
    end
  end

  def verify_dashboard_access(conn, _opts) do
    case Conn.get_session(conn, :user_auth) do
      {"rliebling@coursehero.com", _valid_until, _role} ->
        conn

      {"rliebling@gmail.com", _valid_until, _role} ->
        conn

      {"vmendiluce@coursehero.com", _valid_until, _role} ->
        conn

      {"dev@example.com", _valid_until, _role} ->
        conn

      _ ->
        conn
        |> redirect(to: "/")
        |> Conn.halt()
    end
  end

  pipeline :api do
    plug :accepts, ["json"]
  end

  # Add the following scope ahead of other routes
  # Keep this as a top-level scope and **do not** add
  # any plugs or pipelines explicitly to this scope.
  scope "/sso" do
    forward "/", Samly.Router
  end

  # Health check endpoint for ALB (no authentication required)
  scope "/health", DbPortalWeb do
    get "/", HealthController, :index
  end

  scope "/login", DbPortalWeb do
    pipe_through :browser

    get "/", LoginController, :index
  end

  scope "/", DbPortalWeb do
    pipe_through [:browser, :auth]

    get "/", NavController, :index
    get "/logout", LoginController, :logout

    live_session :user, on_mount: {DbPortalWeb.UserAuthLive, :user} do
      live "/load", PageLive, :index
      live "/readonly", ReadOnlyLive, :index
      live "/awscli_runner", AwscliRunnerLive, :index
      live "/top", TopLive, :index
      live "/unused", UnusedIndexLive, :index
    end
  end

  scope "/", DbPortalWeb do
    pipe_through [:browser, :auth]

    live_session :restricted_user, on_mount: {DbPortalWeb.UserAuthLive, :restricted_user} do
      live "/user_admin", UserAdminLive, :index
      live "/admin", AdminLive, :index

      live "/athena_tools", AthenaTools.AthenaToolsLive, :index
      # if you specify an action here then live_path() won't be defined (but maybe index_path() would be???
      live "/ptosc", PtoscLive, :new_job
      live "/ptosc/launch", PtoscLive, :launch
      live "/ptosc/job/:job_id", PtoscLive, :job
      live "/sampler", SamplerLive, :index
    end

    live_session :break_glass_user, on_mount: {DbPortalWeb.UserAuthLive, :break_glass_user} do
      live "/breakglass", BreakGlassLive, :index
    end
  end

  # Other scopes may use custom stacks.
  # scope "/api", DbPortalWeb do
  #   pipe_through :api
  # end

  import Phoenix.LiveDashboard.Router

  scope "/" do
    pipe_through [:browser, :verify_dashboard_access]
    live_dashboard "/dashboard", metrics: DbPortalWeb.Telemetry, additional_pages: [
      live_view_sessions: DbPortalWeb.LiveViewSessionsLive
    ]
  end
end
