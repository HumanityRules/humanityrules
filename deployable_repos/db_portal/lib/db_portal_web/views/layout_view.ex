defmodule DbPortalWeb.LayoutView do
  use DbPortalWeb, :view

  def permitted_tabs(conn) do
    session = DbPortal.Role.get_session(conn)
    [
      {"Dashboard", "/"},
      {"R/O DB Access", "/readonly"},
      {"Unused Indexes", "/unused"},
      {"DB Top", "/top"},
      {"PTOSC", "/ptosc"},
      {"Athena Tools", "/athena_tools"},
      {"Admin", "/kadmin"}
    ]
    |> Enum.filter(&(is_tab_permitted?(&1, session)))
    |> IO.inspect(label: "permitted_tabs*************")
  end

  defp is_tab_permitted?({_name, _path}, {nil, _until, _role}=_user_auth ), do: false
  defp is_tab_permitted?({_name, path}, {email, _until, _role}=user_auth ) do
    view = String.slice(path, 1..-1//1) # remove initial `/`
    case DbPortalWeb.UserAuthLive.check_page_permission(view, email, user_auth) do
      {:error, _} -> false
      {:ok, _} -> true
    end
  end
end
