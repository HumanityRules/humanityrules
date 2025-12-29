defmodule DbPortalWeb.BreakGlassLive do
  use DbPortalWeb, :live_view

  require Logger

  def nav_selected(), do: "Break Glass"

  @default_user_and_password "********"

  @impl true
  def mount(_params, session, socket) do
    IO.inspect(session, label: "Session")
    {user_email, _, _} = session["user_auth"]

    socket = socket
      |> assign(:env, "prod")
      |> assign_schema(1)
      |> assign(:user_email, user_email)
      |> assign(:db_user_name, @default_user_and_password)
      |> assign(:password, @default_user_and_password)
      |> assign(:disabled, false)
    {:ok, socket}
  end

  @impl true
  def handle_event("break_glass", unsigned_params, socket) do
    IO.inspect(unsigned_params, label: "unsigned_params")

    case DbPortal.BreakGlass.Account.break_glass(socket.assigns.cluster_endpoint, socket.assigns.schema_name, socket.assigns.user_email) do
      {:error, err} ->
        {:noreply, assign(socket, :db_user_name, err)}
      {:ok, db_user_name, password} ->
        {:noreply, assign(socket,
          db_user_name: db_user_name,
          password: password,
          disabled: true)}
    end

  end

  @impl true
  def handle_info({:updated_env, %{env: env, schema: schema}}, socket) do
    socket =
      socket
      |> assign(:env, env)
      |> assign_schema(schema)
    {:noreply, socket}
  end

  @impl true
  def handle_info({:updated_schema, %{schema: schema}}, socket) do
    socket =
      socket
      |> assign_schema(schema)
    {:noreply, socket}
  end

  def assign_schema(socket, schema_id) do
    {cluster_endpoint, schema_name} = get_schema_and_cluster_names(schema_id)
    assign(socket,
      schema: schema_id,
      cluster_endpoint: cluster_endpoint,
      schema_name: schema_name,
      db_user_name: @default_user_and_password,
      password: @default_user_and_password,
      disabled: false)
  end

  def get_schema_and_cluster_names(schema_id) do
    {_readonly, write_endpoint, schema} = DbPortal.DbMetadata.endpoints_and_schema(schema_id)
    {write_endpoint, schema}
  end

end
