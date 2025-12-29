defmodule DbPortalWeb.Ptosc.LaunchComponent do
  use DbPortalWeb, :live_component

  require Logger
  alias DbPortal.Ptosc

  @impl true
  def mount(socket) do
    {:ok, assign(socket,
      job_req: nil,
      dry_run_output: "<not yet run>",
      dry_run_status: :not_successful)}
  end

  @impl true
  def update(assigns, socket) do
    socket = socket |> assign(assigns)

    {:ok, socket}
  end

  @impl true
  def render(assigns) do
    ~H"""
      <div>
        <.modal id="confirm_dry_run" show={@job_req} on_cancel={JS.patch(~p"/ptosc")}
                on_confirm={hide_modal("confirm_dry_run") |> JS.push("do_dry_run", target: @myself)}>
          Are you sure you want to execute this dry run?
          <pre class="p-8 text-gray-600"><%= command(@job_req, :dry_run) %></pre>
          <:confirm>Do It!</:confirm>
          <:cancel>Go Back</:cancel>
        </.modal>
        <div class="p-6 bg-dodgerblue-100">
          DRY RUN
          <div>
          Command:
            <pre class="text-gray-600"><%= command(@job_req, :dry_run) %></pre>
          </div>
          <div class = "mt-4">
          Output:
            <!--
            insert a newline to disguise the fact that the <pre>
            will render the whitespace in the markup since i start the
            elixir part on the next line
            -->
            <pre class={"whitespace-pre-wrap #{dry_run_status_class(@dry_run_status)}"}>
              <%= "\n" <> @dry_run_output %>
            </pre>
          </div>
          <.button disabled={@dry_run_status != :ok} phx-click="start_ptosc" phx-target={@myself}>
            Start PTOSC
          </.button>
          <.button phx-click={JS.patch(~p"/ptosc")}>
            Back
          </.button>
        </div>
      </div>
    """
  end

  @impl true
  def handle_event("do_dry_run", _parms, socket) do
    cmd = command(socket.assigns.job_req, :dry_run)
    socket = case Ptosc.start_dryrun(cmd) do
      {:ok, msg} ->
        assign(socket, dry_run_status: :ok, dry_run_output: msg)
      {:error, msg } ->
        assign(socket, dry_run_status: :not_successful, dry_run_output: msg)
    end
    {:noreply, socket}
  end

  @impl true
  @doc """
  For the start_ptosc event, we initiate the PTOSC job and transition
  to the job tracker component
  """
  def handle_event("start_ptosc", _parms, socket) do
    job_req = socket.assigns.job_req
    socket = with {:ok, _tracker_pid, job} <- Ptosc.start_job(job_req) do
      Logger.info "Successfuly started ptosc"
      socket
      |> put_flash(:info, "Successfully started PTOSC full run")
      |> push_patch(to: ~p"/ptosc/job/#{job.id}" )
    else
      {:error, msg, _job_with_statuses} ->
        Logger.warning("Failed starting PTOSC job with error: #{msg}")
        put_flash(socket, :error, "Failed executing PTOSC full run: #{msg}" )
        |> assign(:dry_run_output, msg)
    end
    {:noreply, socket}
  end

  def command(nil, :dry_run), do: nil
  def command(job_req, :dry_run) do
    Ptosc.command(job_req, "dry-run")
  end
  def command(job_req, :execute) do
    Ptosc.command(job_req, "execute")
  end

  def dry_run_status_class(:ok), do: ""
  def dry_run_status_class(:not_successful), do: "text-red-400"

end

