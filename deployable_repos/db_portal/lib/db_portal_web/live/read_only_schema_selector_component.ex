defmodule DbPortalWeb.ReadOnlySchemaSelectorComponent do
  use Phoenix.LiveComponent
  import Phoenix.HTML
  import Phoenix.HTML.Form
  use PhoenixHTMLHelpers

  @impl true
  def render(assigns) do
    ~H"""
    <div class="flex w-full rounded-t-lg">
      <form class="flex items-center justify-between w-full mx-auto" phx-change="schema_changed" phx-target={ @myself } >
        <div class="flex flex-wrap my-1 -mx-3">
          <div class="flex flex-wrap px-3 mx-2 my-1 ">
            <label class="mx-2 my-2 text-sm font-bold tracking-wide text-gray-700 uppercase text-md" for="grid-first-name">
              Env:
            </label>
            <select name="env" class="text-sm text-base bg-white border-gray-300 focus:outline-none focus:ring-ch-blue focus:border-ch-blue sm:text-sm rounded-md">
              <%= for e <- @envs do %>
                <option value={ e }
                  selected={selected_attr(@env, e)} >
                  <%= e %>
                </option>
              <% end %>
            </select>
          </div>
          <div class="flex flex-wrap px-3 mx-2 my-1 ">
            <label class="my-2 mr-2 text-sm font-bold tracking-wide text-gray-700 uppercase text-md" for="grid-first-name">
              Schema:
            </label>
            <select name="schema" class="text-sm text-base bg-white border-gray-300 focus:outline-none focus:ring-ch-blue focus:border-ch-blue sm:text-sm rounded-md">
              <%= for s <- @schemas do %>
                <option value={ s.id }
                  selected={selected_attr(@schema, s.id) } >
                <%= s.name <> "{" <> s.cluster.name <> ")"%>
                </option>
              <% end %>
            </select>
          </div>
        </div>

      </form>

      <!-- gear icon -->
      <div class="relative flex items-center ml-3 mr-6" x-data="dropdown()">
        <div>
          <button class="flex items-center max-w-xs text-sm text-white rounded-full focus:outline-none focus:shadow-solid" id="gear-menu" aria-label="User menu" aria-haspopup="true" @click="open()">
            <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" version="1.1" id="Layer_1" stroke="ch-blue" viewBox="340 140 280 279.416" xml:space="preserve" class="w-6 h-6">
              <path d="M620,305.666v-51.333l-31.5-5.25c-2.333-8.75-5.833-16.917-9.917-23.917L597.25,199.5l-36.167-36.75l-26.25,18.083  c-7.583-4.083-15.75-7.583-23.916-9.917L505.667,140h-51.334l-5.25,31.5c-8.75,2.333-16.333,5.833-23.916,9.916L399.5,163.333  L362.75,199.5l18.667,25.666c-4.083,7.584-7.583,15.75-9.917,24.5l-31.5,4.667v51.333l31.5,5.25  c2.333,8.75,5.833,16.334,9.917,23.917l-18.667,26.25l36.167,36.167l26.25-18.667c7.583,4.083,15.75,7.583,24.5,9.917l5.25,30.916  h51.333l5.25-31.5c8.167-2.333,16.333-5.833,23.917-9.916l26.25,18.666l36.166-36.166l-18.666-26.25  c4.083-7.584,7.583-15.167,9.916-23.917L620,305.666z M480,333.666c-29.75,0-53.667-23.916-53.667-53.666s24.5-53.667,53.667-53.667  S533.667,250.25,533.667,280S509.75,333.666,480,333.666z"/>
            </svg>
          </button>
          <div class="absolute right-0 w-48 mt-2 shadow-lg origin-top-right rounded-md" x-show="isOpen()" @click.away="close()">
            <div class="py-1 bg-white rounded-md shadow-xs" role="menu" aria-orientation="vertical" aria-labelledby="gear-menu">
              <a phx-click="refresh_schemas" phx-target= {@myself}  class="block px-4 py-2 text-sm text-gray-700 hover:bg-gray-100" role="menuitem">Refresh Schemas</a>
            </div>
          </div>
        </div>
      </div>
    </div>
    """
  end

  @impl true
  def mount(socket) do
    {:ok, socket}
  end

  @impl true
  def update(%{envs: envs, env: env, schema: schema}=parms, socket) do
    permitted_schema_ids = Map.get(parms, :permitted_schema_ids) |> IO.inspect(label: "ReadOnlySchemaSelectorComponent.update", charlists: :as_lists)
    schema = if is_nil(schema) do
      s = DbPortal.DbMetadata.get_initial_schema(env)
      schema_id = if s, do: s.id, else: nil
      notify_new_schema(schema_id)
      schema_id
    else
      schema
    end

    socket =
      socket
      |> assign(:env, env)
      |> assign(:envs, envs)
      |> assign(:schema, schema)
      |> assign(:permitted_schema_ids, permitted_schema_ids)
      |> assign(:schemas, schemas_for_env(env, permitted_schema_ids))

    {:ok, socket}
  end

  @impl true
  def handle_event("schema_changed", %{"_target"=>["env"], "env" => env}=p, socket) do
    IO.inspect p, label: "schema_changed(env) pid=#{inspect self()}"

    new_schemas = schemas_for_env(env, socket)
    socket =
      socket
      |> assign(:schemas, new_schemas)

    case new_schemas do
      [] -> nil
      list -> send( self(), {:updated_env, %{env: env, schema: hd(list).id}})
              |> IO.inspect(label: "sending msg ********** #{inspect self()}")
    end

    {:noreply, socket}
  end

  @impl true
  def handle_event("schema_changed", %{"_target"=>["schema"], "schema" => schema}=p, socket) do
    IO.inspect p, label: "schema_changed"
    schema = String.to_integer(schema) |> IO.inspect(label: "sending msg schema changed")

    notify_new_schema(schema)

    {:noreply, socket}
  end

  @impl true
  def handle_event("refresh_schemas", %{}, socket) do
    env = socket.assigns[:env]
    IO.inspect env, label: "refresh_schemas(env)"
    DbPortal.DbMetadata.refresh(env)

    {:noreply, assign(socket, :schemas, schemas_for_env(env, socket))}
  end
  def schemas_for_env(env, %{assigns: %{permitted_schema_ids: permitted_schema_ids}}=_socket) do
      DbPortal.DbMetadata.schemas(env, permitted_schema_ids)
  end
  def schemas_for_env(env, permitted_schema_ids) do
      DbPortal.DbMetadata.schemas(env, permitted_schema_ids)
  end

  defp notify_new_schema(schema) do
    send self(), {:updated_schema, %{schema: schema}}
  end

  defp selected_attr(opt, opt), do: true
  defp selected_attr(_not_o, _o), do: false
end
