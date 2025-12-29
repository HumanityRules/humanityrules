defmodule DbPortalWeb.NavController do
  use DbPortalWeb, :controller

  def index(conn, %{"path"=>path}=params) do
    qry = Map.get(params, "qry", "")

    redirect(conn, to: URI.to_string(%URI{path: URI.decode_www_form(path), query: qry}))
  end

  def index(conn, _params) do
    assigns = %{}
    render(conn, "index.html", assigns)
  end
end
