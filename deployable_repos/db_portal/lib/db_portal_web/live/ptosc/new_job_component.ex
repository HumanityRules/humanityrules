defmodule DbPortalWeb.Ptosc.NewJobComponent do
  @moduledoc """
  NewJob is the live component for the new PTOSC job form
  """
  use DbPortalWeb, :live_component

  alias DbPortal.Ptosc

  defmodule PtoscRequest do
    use Ecto.Schema

    @primary_key false
    embedded_schema do
      field :table, :string
      field :alter, :string
      field :schema_name, :string
      field :host, :string
      field :schema, :integer
      field :user, :string
      field :ticket, :string
      field :chunk_time, :decimal, default: 0.070
    end

    def changeset(attrs, schema, user) do
      %PtoscRequest{}
      |> Ecto.Changeset.cast(%{schema: schema, user: user}, [:schema, :user])
      |> Ecto.Changeset.cast(attrs, [:table, :alter, :host, :schema_name, :ticket, :chunk_time])
      |> Ecto.Changeset.validate_required([:table, :alter, :schema, :host, :schema_name, :user, :ticket, :chunk_time])
      |> Ecto.Changeset.validate_number(:chunk_time, greater_than: 0.0, less_than: 3.0)
      |> Ecto.Changeset.validate_format(:alter, ~r/\A[^`\r\n]+\Z/, message: "avoid backticks and newlines in alter command")
      |> Ecto.Changeset.validate_format(:alter, ~r/\A(?!.*alter table).*/i, message: "start with what comes after 'ALTER TABLE tablename '")
    end

  end

  @default_table_status %{"Rows"=> -1, "Data_length"=>0, "Index_length"=>0}

  @impl true
  def mount(socket) do
    changeset = PtoscRequest.changeset(%{}, nil, "not-yet-set@example.com")
    {:ok, assign(socket,
      form: to_form(changeset),
      create_table: nil,
      command: nil,
      table_status: @default_table_status,
      job_req: nil
    )}
  end

  @impl true
  def update(%{schema: schema} = assigns , socket) when schema != nil do
    IO.inspect(assigns, label: "update schema in new_job")
    socket = assign(socket, assigns)

    {_read, write, schema_name} = DbPortal.DbMetadata.endpoints_and_schema(schema)

    form = socket.assigns.form.source
      |> Ecto.Changeset.put_change(:host, write)
      |> Ecto.Changeset.put_change(:schema_name, schema_name)
      |> to_form()
    {:ok, assign(socket, form: form)}
  end

  def update(assigns , socket) do
    socket = assign(socket, assigns)
    {:ok, socket}
  end

  @impl true
  def handle_event("validate", %{"ptosc_request"=>parms}, socket) do
    IO.inspect(parms, label: "handle_event(validate)")
    changeset =
      PtoscRequest.changeset(parms, socket.assigns.schema, socket.assigns.email)
      |> Map.put(:action, :insert)

    {changeset, socket} = case Ecto.Changeset.fetch_change(changeset, :table) do
      :error -> {changeset, socket}
      _ ->
        {cs, create_table, table_status} = validate_table(changeset)
        {cs, assign(socket,
          create_table: create_table,
          table_status: table_status)
        }
    end

    command = alter_command(parms, socket)
    job_req = case Ecto.Changeset.apply_action(changeset, :dry_run) do
      {:ok, job_req} -> job_req
      _ -> nil
    end
    {:noreply, assign(socket,
      command: command,
      job_req: job_req,
      form: to_form(changeset)
    )}
  end

  @impl true
  def handle_event("dry_run", %{"ptosc_request"=>parms}, socket) do
    case PtoscRequest.changeset(parms, socket.assigns.schema, socket.assigns.email) |> Ecto.Changeset.apply_action(:insert) do

      {:ok, job_req} ->
        #{:noreply, socket |> push_patch(to: ~p"/ptosc/launch")}
        parent_liveview_pid = self() # same process since this is just a component
        send(parent_liveview_pid, {:do_dry_run, job_req})
        {:noreply, socket }

      {:error, changeset} ->
        socket = put_flash(socket, :error, "Invalid entries")
                 |> assign(form: to_form(changeset))
        {:noreply, socket}
    end
  end

  defp validate_table(%Ecto.Changeset{} = changeset) do
    table = Ecto.Changeset.get_field(changeset, :table)
    schema = Ecto.Changeset.get_field(changeset, :schema)
    case table && table_info(schema, table) do
      {:ok, sct, status} -> {changeset, sct, status}
      {:error, _err} -> {ensure_error(changeset, :table, "invalid table"), "", @default_table_status}
      nil -> {ensure_error(changeset, :table, "cannot not be blank"), "", @default_table_status}

    end
  end

  defp alter_command(parms, socket) do
    case PtoscRequest.changeset(parms, socket.assigns.schema, socket.assigns.email) |> Ecto.Changeset.apply_action(:insert) do
      {:ok, job_req} -> Ptosc.command(job_req, "dry-run")
      {:error, _invalid_changeset} -> "Fix form to see" #"INVALID: #{inspect invalid_changeset}"
    end
  end

  defp table_info(schema, table) do
    with {:ok, sct} <- DbPortal.MonitoredDbRepo.show_create_table(schema, table),
         {:ok, status} <- DbPortal.MonitoredDbRepo.table_status(schema, table) do
      {:ok, sct, status}
    end
  end

  defp ensure_error(changeset, field, msg) do
    if Enum.any?(changeset.errors, fn
      {^field, {^msg, _}} -> true
      _ -> false
    end) do
        changeset
    else
        Ecto.Changeset.add_error(changeset, field, msg)
    end
  end

  @impl true
  def render(assigns) do
    ~H"""
      <div class="flex">
        <section class="w-2/3 resize-x overflow-auto">
          <.form for={@form} phx-change="validate" phx-submit="dry_run" id="new_job_form" phx-target={@myself} class="h-full w-full">
            <div class="p-6 mt-4 bg-dodgerblue-100 h-full w-full">
              <h3 class="text-2xl text-blue-800">Inputs</h3>
              <div class="mt-2">
                <div>
                  <div class="">
                    <%= label(@form, :ticket) %>
                    <%= error_tag(@form, :ticket) %>
                    <%= text_input(@form, :ticket, class: "form-input mt-1 block w-full") %>
                  </div>

                  <div class="mt-6">
                    <%= label(@form, :table) %>
                    <%= error_tag(@form, :table) %>
                    <%= text_input(@form, :table,
                      class: "form-input mt-1 block w-full",
                      phx_debounce: "blur"
                    ) %>
                  </div>

                  <div class="mt-6">
                    <%= label(@form, :chunk_time, "Chunk time (secs)") %>
                    <%= error_tag(@form, :chunk_time) %>
                    <%= number_input(@form, :chunk_time,
                      class: "form-input mt-1 block w-full"
                    ) %>
                  </div>

                  <div class="mt-6">
                    <%= label(@form, :alter) %>
                    <%= error_tag(@form, :alter) %>
                    <%= textarea(@form, :alter,
                      phx_debounce: "blur",
                      class: "form-textarea mt-1 block w-full",
                      rows: 3
                    ) %>
                  </div>
                </div>
              </div>
              <.button phx-disable-with="Performing dry run..." disabled={!@form.source.valid?}
                class=
                "form-button block font-semibold disabled:opacity-50 bg-ch-blue text-white rounded-lg mt-3 px-4 py-2 disabled:cursor-not-allowed"
              >
              Perform dry run
              </.button>
            </div>

            <%= text_input(@form, :host, class: "hidden") %>
            <%= text_input(@form, :schema_name, class: "hidden") %>
          </.form>
        </section>

        <!-- Info section -->
        <section class="flex-1 basis-1/3 overflow-auto mt-4 ml-5 p-3 bg-gray-100">
          <h3 class="text-2xl text-blue-800">Results</h3>

          <div class="p-2">
            <div>
              Target DB instance:
            </div>
            <div class="px-3 text-xs text-gray-600 border">
              <%= text_input(:foo, :host,
                class: "form-input mt-1 block w-full",
                readonly: true,
                tabindex: -1,
                value: Ecto.Changeset.get_field(@form.source, :host)
              ) %>
            </div>

            <div class="mt-3">
              Current Create Table
            </div>
            <pre class="h-56 p-3 whitespace-pre-wrap overflow-auto text-sm text-gray-600 border"><%= @create_table %></pre>

            <div class="mt-3">
              Command
            </div>
            <pre class="h-40 p-3 whitespace-pre-wrap overflow-auto text-sm text-gray-600 border"><%= @command %></pre>

            <div class="mt-3">
              Size
            </div>
            <div class="p-3 overflow-auto text-gray-600 border">
              Rows: <%= @table_status["Rows"]
              |> Number.Delimit.number_to_delimited(precision: 0) %>
              <br />
              MBytes: <%= ((@table_status["Data_length"] + @table_status["Index_length"]) /
                              1_000_000)
              |> round()
              |> Number.Delimit.number_to_delimited(precision: 0) %>
            </div>
          </div>
        </section>
      </div>
    """
  end

end
