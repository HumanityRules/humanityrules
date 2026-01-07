defmodule DbPortalWeb.HealthController do
  @moduledoc """
  Health check controller for ALB health checks.
  Returns 200 OK when the application is running.
  """
  use DbPortalWeb, :controller

  def index(conn, _params) do
    conn
    |> put_resp_content_type("application/json")
    |> send_resp(200, Jason.encode!(%{status: "ok"}))
  end
end

