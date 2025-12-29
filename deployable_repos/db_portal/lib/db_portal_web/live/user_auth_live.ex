defmodule DbPortalWeb.UserAuthLive do
  @moduledoc ~S"""
    Manage authenticating within Liveviews.
  """
  import Phoenix.LiveView
  import Phoenix.Component

  def on_mount(:user, _params, %{"user_auth" => {email, valid_until, _}}, socket) do
    avatar = DbPortalWeb.Avatar.get(email)
    socket = assign(socket, email: email, avatar: avatar)

    if DateTime.compare(valid_until, DateTime.utc_now()) == :gt do
      {:cont, socket}
    else
      {:halt, redirect(socket, to: "/login")}
    end
  end

  def on_mount(
        :restricted_user,
        params,
        %{"user_auth" => {email, valid_until, _groups} = user_auth} = session,
        socket
      ) do
    IO.inspect({params, session}, label: "on_mount(restricted_user)")

    avatar = DbPortalWeb.Avatar.get(email)
    socket = assign(socket, email: email, avatar: avatar)
    view = Map.get(socket, :view)

    with :ok <- session_still_valid(valid_until, DateTime.utc_now()),
         # TODO: fix the nil for needed_role
         {:ok, permitted_schemas} <- check_page_permission(view, email, user_auth) do

      permitted_schema_ids =
        if permitted_schemas == :all do
          :all
        else
          Enum.map(permitted_schemas, & &1.schema_id)
        end

      {:cont, assign(socket, :permitted_schema_ids, permitted_schema_ids)}
    else
      {:error, msg} ->
        socket = put_flash(socket, :error, msg)
        {:halt, redirect(socket, to: "/login")}
    end
  end

  def on_mount(
        :break_glass_user,
        params,
        %{"user_auth" => {email, valid_until, _groups} = _user_auth} = session,
        socket
      ) do
    IO.inspect({params, session}, label: "on_mount(break_glass_user)")

    avatar = DbPortalWeb.Avatar.get(email)
    socket = assign(socket, email: email, avatar: avatar)

    with :ok <- session_still_valid(valid_until, DateTime.utc_now()),
         true <- DbPortal.BreakGlass.AuthorizedUser.is_authorized?(email) do

      {:cont, socket}
    else
      {:error, msg} ->
        socket = put_flash(socket, :error, "Not authorized to break glass. #{msg}")
        {:halt, redirect(socket, to: "/login")}
      false ->
        socket = put_flash(socket, :error, "Not authorized to break glass.")
        {:halt, redirect(socket, to: "/")}
    end
  end

  # always allow admin access in dev (recognized by the email addr)
  def check_page_permission("kadmin", "dev@example.com", _user_auth), do: {:ok, nil}
  def check_page_permission(path_or_view, nil, _user_auth) do
    needed_role = needed_role_for_view(path_or_view)
    IO.inspect({path_or_view, nil, needed_role}, label: "check_page_permission")

    if is_nil(needed_role) do
      {:ok, :all}
    else
      {:error, "not logged in"}
    end
  end

  def check_page_permission(path_or_view, email, user_auth) do
    needed_role = needed_role_for_view(path_or_view)
    IO.inspect({path_or_view, email, needed_role}, label: "check_page_permission")

    case DbPortal.Role.satisfies_need?(needed_role, {email, nil, user_auth}) do
      true ->
        {:ok, :all}

      false ->
        case DbPortal.Repo.all(DbPortal.Permission.permitted_schemas_query(email, path_or_view))|>IO.inspect(label: "permitted query result") do
          [] -> {:error, "Not allowed access to #{path_or_view}"}
          permitted_schemas -> {:ok, permitted_schemas}
        end
    end |> IO.inspect(label: "check_page_permission result")
  end

  defp needed_role_for_view(path_or_view) do
    case path_or_view do
      "ptosc" -> :data_engr
      DbPortalWeb.PtoscLive -> :data_engr

      "athena_tools" -> :data_engr
      DbPortalWeb.AthenaTools.AthenaToolsLive -> :data_engr

      "admin" -> :data_engr
      _ -> nil
    end
  end

  defp session_still_valid(valid_until, now) do
    if DateTime.compare(valid_until, now) == :gt,
      do: :ok,
      else: {:error, "Your session has expired."}
  end

end
