defmodule DbPortalWeb.PtoscLive do
  use DbPortalWeb, :live_view

  require Ecto.Query
  require Logger

  alias DbPortal.Ptosc
  alias DbPortal.Ptosc.Tracker

  def nav_selected(), do: "PTOSC"

  @impl true
  def mount(_parms, %{"user_auth"=>{email, _, _}} = _session, socket) do
    #IO.puts "PtoscLive.mount ################### self=#{inspect self()} connected?=#{connected?(socket)} parms=#{inspect parms} session=#{inspect session}"
    #IO.puts("get_connect_params: #{inspect get_connect_params(socket)}")

    timezone = (get_connect_params(socket)["timezone"] || "UTC")

    initial_env = "local"

    jobs = if connected?(socket), do: Ptosc.get_recent_jobs(), else: []
    if connected?(socket), do: DbPortalWeb.Endpoint.subscribe(Ptosc.Tracker.joblist_topic())

    socket = socket
             |> assign(
               env: initial_env,
               schema: nil,
               email: email,
               parms: %{},
               progress: [],
               job_req: nil,
               resource_id: nil,
               state: nil,
               timezone: timezone,
               jobs: jobs
             )
    {:ok, socket}
  end

  @impl true
  def handle_params(%{"job_id"=>job_id} = parms, _uri, socket) do
    parsed_job_id = Integer.parse(job_id)
    socket = cond do
     socket.assigns.live_action != :job ->
        push_patch(socket, to: ~p"/ptosc")

     :error == parsed_job_id || elem(parsed_job_id, 1) != "" ->
        push_patch(socket, to: ~p"/ptosc")

      true ->
        DbPortalWeb.Endpoint.subscribe(Ptosc.Tracker.job_name(job_id))
        progress = Ptosc.Tracker.updates(job_id)

        assign(socket,
          progress: progress,
          subscribed_job_id: String.to_integer(job_id),
          state: Ptosc.current_status(String.to_integer(job_id)),
          parms: parms)
    end

    {:noreply, socket}
  end

  @impl true
  def handle_params(_p, _uri, socket) do
    IO.inspect(self(), label: "handle_params ptosc")

    if Map.get(socket.assigns, :subscribed_job_id) do
      socket.assigns.subscribed_job_id
      |> Ptosc.Tracker.job_name()
      |> DbPortalWeb.Endpoint.unsubscribe()
    end

    {:noreply, apply_action(socket, socket.assigns.live_action)}
  end

  @impl true
  def handle_info({:updated_env, %{env: env, schema: schema}}, socket) do
    IO.inspect "updated_env #{env}"
    socket =
      socket
      |> assign(:env, env)
      |> assign_schema(schema)

    {:noreply, socket}
  end

  @impl true
  def handle_info({:updated_schema, %{schema: schema}}, socket) do
    IO.inspect schema, label: "updated_schema in ptosc"

    {:noreply, assign_schema(socket, schema)}
  end


  @impl true
  def handle_info(%{event: "job_progress", payload: %{output: progress}}, socket) do
    {:noreply, socket |> assign(:progress, progress) }
  end

  @impl true
  def handle_info(%{event: "job_status_update", payload: %{job: job}}, socket) do
    socket = if job.id == socket.assigns.subscribed_job_id do
      assign(socket, state: Ptosc.current_status(job))
    else
      socket
    end

    {:noreply, socket |> assign(:jobs, Ptosc.get_recent_jobs()) }
  end

  @impl true
  def handle_info({:do_dry_run, job_req} = m, socket) do
    IO.inspect m, label: "Ptosc.handle_info do_dry_run self=#{inspect self()}"
    {:noreply, socket |> assign(job_req: job_req) |> push_patch(to: ~p"/ptosc/launch") }
  end

  @impl true
  def handle_info(msg, socket) do
    IO.inspect msg, label: "Ptosc.handle_info generic self=#{inspect self()}"
    {:noreply, socket }
  end

  defp assign_schema(socket, nil) do
    IO.puts("assign_schema nil")
    assign(socket, schema: nil, resource_id: nil)
  end
  defp assign_schema(socket, schema) do
    IO.inspect(schema, label: "assign_schema ")
    socket = assign(socket, :schema, schema)
    case DbPortal.DbMetadata.resource_id_for_writer(schema) do
      {:error, :no_such_schema} ->
        assign(socket, resource_id: "")
        |> put_flash(:error, "No such schema is found.  Please select a valid schema.")

      {:error, :bad_network} ->
        assign(socket, resource_id: "")

      {:error, :expired_token} ->
        assign(socket, resource_id: "")

      resource_id ->
        assign(socket, resource_id: resource_id)
    end
  end

  def job_status_color(job) do
    case Ptosc.current_status(job) do
      "started" -> "bg-green-500"
      "unpaused" -> "bg-green-500"
      "paused" -> "bg-yellow-500"
      "ended" -> "bg-green-500"
      :none_found -> "bg-gray-500"
    end
  end
  @doc ~s"""
    handle the icon for each of these possible statuses
      %Tracker{status: "ended", extra: "success"},
      %Tracker{status: "ended", extra: "Failed"},
      %Tracker{status: "paused", extra: "success"},
      %Tracker{status: "unpaused", extra: "success"},
      %Tracker{status: "started", extra: "success"}
  """
  def job_status(assigns) do
    job = assigns.job
    case Tracker.current_status(job) do
      %Tracker{status: "ended", extra: "success"} ->
        ~H"""
          <.icon name="hero-check-circle-solid"
            class={"w-4 h-4 bg-green-500"}/>
        """
      %Tracker{status: "ended"}  ->
        ~H"""
          <.icon name="hero-x-circle-solid"
            class={"w-4 h-4 bg-red-500"}/>
        """
      %Tracker{status: "paused"}  ->
        ~H"""
          <.icon name="hero-pause-circle"
            class={"w-4 h-4 bg-green-500"}/>
        """
      _ -> ~H"""
            <span class={"w-4 h-4 bg-green-500 rounded-full"}> </span>
          """
    end
  end

  def job_link_body(job) do
    assigns = %{j: job}
    ~H"""
      <div class="inline-flex items-center justify-between w-full overflow-hidden ">
        <span class="font-semibold leading-none text-gray-700 overflow-ellipsis" ><%= @j.table %></span>
        <.job_status job={@j}></.job_status>
      </div>
      <div class="inline-flex items-center justify-between w-full text-gray-600">
        <span class="overflow-ellipsis" ><%= @j.schema_name %></span>
        <span class="overflow-ellipsis"><%=@j.user|>String.split("@")|>hd %></span>
      </div>
    """
  end

  defp apply_action(socket, :new_job) do
    socket
    |> assign(:page_title, "New Job")
  end

  defp apply_action(socket, :launch) do
    socket
    |> assign(:page_title, "Launch PTOSC")
  end

  defp apply_action(socket, :job) do
    socket
    |> assign(:page_title, "PTOSC Job")
  end

end
