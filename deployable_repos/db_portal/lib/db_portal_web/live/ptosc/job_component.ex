defmodule DbPortalWeb.Ptosc.JobComponent do
  @moduledoc """
  JobComponent is the live component for tracking a PTOSC job
  """
  use DbPortalWeb, :live_component
  alias DbPortal.Ptosc

  @impl true
  def mount(socket) do
    {:ok, socket}
  end

  @impl true
  def update(assigns, socket) do
    socket = socket
      |> assign(assigns)
    job_id = Map.get(socket.assigns.parms, "job_id")
    job = Ptosc.Job.get_with_statuses(job_id)
    socket = socket
      |> assign(job_id: job_id, job: job)

    {:ok, socket}
  end

  @impl true
  def handle_event("toggle_pause", %{"job_id" => job_id} = _parms, socket) do
    job_id = String.to_integer(job_id)
    {result, action} = case socket.assigns.state do
      "started" ->
        {Ptosc.pause(job_id), "pause"}
      "unpaused" ->
        {Ptosc.pause(job_id), "pause"}
      "paused" ->
        {Ptosc.unpause(job_id), "unpause"}
      "ended" ->
        {:ok, "pause"}
    end
    socket = case result do
      :ok -> assign(socket, status: Ptosc.current_status(job_id) )
      {:error, e} -> put_flash(socket, :error, "Failed to #{action}: #{inspect e}")
    end
    {:noreply, socket}
  end

  @impl true
  def render(assigns) do
    ~H"""
    <div class="flex">
      <section class="w-2/3 p-3">
        <h3 class="text-lg font-medium text-gray-900 leading-6" >
          Job Status Updates
        </h3>
        <div class="max-h-screen mt-4 overflow-y-scroll">
          <ul class="text-sm text-gray-500 font-mono leading-5">
            <%= for p <- @progress do %>
              <li><%= p %></li>
            <% end %>
          </ul>
        </div>
        <div class="py-3 bg-gray-50 sm:flex space-x-4">
          <span
            :if={@state != "ended"}
            class="flex w-full rounded-md shadow-sm sm:w-auto">

            <button type="button"
              phx-click="toggle_pause"
              phx-target={@myself}
              phx-value-job_id={@job_id}
              class="inline-flex justify-center w-full px-4 py-2 text-base font-medium text-white bg-red-600 border border-transparent rounded-md leading-6 shadow-sm hover:bg-red-500 focus:outline-none focus:border-red-700 focus:shadow-outline-red transition ease-in-out duration-150 sm:text-sm sm:leading-5 disabled:opacity-50">

              <%= if @state=="paused", do: "Unpause", else: "Pause" %>
            </button>
          </span>
          <span
              :if={@state == "ended"}
              class="flex w-full rounded-md shadow-sm sm:w-auto">

            <button type="button"
              phx-click={JS.patch(~p"/ptosc")}
              class="inline-flex justify-center w-full px-4 py-2 text-base font-medium text-white bg-red-600 border border-transparent rounded-md leading-6 shadow-sm hover:bg-red-500 focus:outline-none focus:border-red-700 focus:shadow-outline-red transition ease-in-out duration-150 sm:text-sm sm:leading-5 disabled:opacity-50">
              Back
            </button>
          </span>
          <span :if={@state != "ended"}
              class="flex w-full mt-3 rounded-md shadow-sm sm:mt-0 sm:w-auto">
            <.link patch={~p"/ptosc"} class="py-2 hover:underline">
              Back
            </.link>
          </span>
        </div>
        <div>
        <p class="alert alert-danger" role="alert"><%= live_flash(@flash, :error) %></p>
        </div>

      </section>
      <section class="w-1/3 p-3 bg-blue-100">
        <ul class="space-y-6">
          <li>
            <h3 class="text-lg text-gray-900">Status</h3>
            <div class="text-sm text-gray-500"><%=  Ptosc.current_status(@job) %></div>
          </li>
          <li>
            <h3 class="text-lg text-gray-900">Started at</h3>
            <div class="text-sm text-gray-500"><%= started_at(@job, @timezone) %> (<%= @timezone %>) </div>
          </li>
          <li>
            <h3 class="text-lg text-gray-900">Elapsed</h3>
            <div class="text-sm text-gray-500"><%= elapsed(@job, @timezone) %></div>
          </li>
          <li>
            <h3 class="text-lg text-gray-900">Command</h3>
            <pre class="text-sm whitespace-pre-wrap text-gray-500"><%= Ptosc.command(@job, :dry_run)%></pre>
          </li>
        </ul>

      </section>
    </div>
    """
  end

  defp started_at_utc(job) do
    job.statuses
    |> Enum.find(fn s -> s.status == "started" end)
    |> then(fn s -> s.status_at end)
  end

  defp started_at(job, timezone) do
    started_at_utc(job)
    |> then(fn t -> t
      |> DateTime.from_naive!("UTC")
      |> DateTime.shift_zone!(timezone) end
    )
  end

  defmodule TimeFormatter do
    def format_time(seconds) do
      {days, remaining} = divrem(seconds, 86400)
      {hours, remaining} = divrem(remaining, 3600)
      {minutes, seconds} = divrem(remaining, 60)

      cond do
        days > 0 ->
          "#{days} day#{pluralize(days)} #{hours}:#{zero_pad(minutes)}:#{zero_pad(seconds)}"

        hours > 0 ->
          "#{hours}:#{zero_pad(minutes)}:#{zero_pad(seconds)}"

        minutes > 0 ->
          "#{zero_pad(minutes)}:#{zero_pad(seconds)}"

        true ->
          "#{seconds} seconds"
      end
    end

    defp divrem(dividend, divisor) do
      quotient = div(dividend, divisor)
      remainder = rem(dividend, divisor)
      {quotient, remainder}
    end

    defp pluralize(1), do: ""
    defp pluralize(_), do: "s"

    defp zero_pad(number) when number < 10, do: "0#{number}"
    defp zero_pad(number), do: "#{number}"
  end

  defp elapsed(job, _timezone) do
    started = started_at_utc(job)
    ended = case Ptosc.current_status(job) do
      "ended" -> job.statuses
        |> Enum.find(& &1.status == "ended")
        |> then(& &1.status_at)

      _ -> NaiveDateTime.utc_now()
    end
    NaiveDateTime.diff(started, ended) |> TimeFormatter.format_time()
  end
end
