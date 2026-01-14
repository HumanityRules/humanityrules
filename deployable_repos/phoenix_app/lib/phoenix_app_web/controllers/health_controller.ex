defmodule PhoenixAppWeb.HealthController do
  use Phoenix.Controller, formats: [:json]

  alias PhoenixApp.Repo

  def index(conn, _params) do
    # Check database connectivity
    case Ecto.Adapters.SQL.query(Repo, "SELECT 1") do
      {:ok, _result} ->
        conn
        |> put_status(:ok)
        |> json(%{status: "healthy", database: "connected"})

      {:error, _reason} ->
        conn
        |> put_status(:service_unavailable)
        |> json(%{status: "unhealthy", database: "disconnected"})
    end
  end
end
