defmodule PhoenixAppWeb.Router do
  use Phoenix.Router

  import Plug.Conn
  import Phoenix.Controller

  pipeline :api do
    plug :accepts, ["json"]
  end

  scope "/", PhoenixAppWeb do
    pipe_through :api

    get "/health", HealthController, :index

    resources "/posts", PostController, except: [:new, :edit]
  end
end
