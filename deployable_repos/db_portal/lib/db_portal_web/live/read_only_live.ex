defmodule DbPortalWeb.ReadOnlyLive do
  use DbPortalWeb, :live_view

  alias __MODULE__

  require Logger
  require Ecto.Query

  alias DbPortalWeb.ReadOnlyLive

  #alias NimbleCSV.RFC4180, as: CSV
  NimbleCSV.define(CSV, separator: ",", escape: "\"", line_separator: "%0A")

  use Ecto.Schema

  @primary_key false
  embedded_schema do
    field :history, :string
    field :query, :string
  end
  def changeset(orig_changeset, attrs) do
    orig_changeset
    |> Ecto.Changeset.cast(attrs, [:query, :history], empty_values: ["", nil])
  end

  def nav_selected(), do: "R/O DB Access"

  @impl true
  def mount(_params, %{"user_auth"=> {email, _, role}} = _session, socket) do
    IO.puts "****** MOUNT *****#{inspect role}"

    init_history_list = []

    default_query = """
      select * from foo
      where bar='baz'
      limit 10
      """

    changeset = changeset(%ReadOnlyLive{}, %{query: default_query})
    socket =
      socket
      |> assign(:email, email)
      |> assign(:cols, [])
      |> assign(:rows, [])
      |> assign(:form, changeset |> to_form())
      |> assign(:prev_form, Ecto.Changeset.apply_changes(changeset))
      |> assign(:history_list, init_history_list)
      |> assign(:err, nil)
      |> assign(:env, "prod")
      |> assign(:schema, 1)

    {:ok, socket}
  end

  @impl true
  def handle_params(_params, _uri, socket) do
    {:noreply, update_history_list(socket)}
  end

  @impl true
  def handle_event("validate", %{"read_only_live"=>parms, "live_monaco_editor" => %{"query"=>_ignore_because_broken_here}}, socket) do
    changeset = changeset(socket.assigns.prev_form, parms)
    socket = if new_history_selection = Ecto.Changeset.get_change(changeset, :history) do # history changed
        LiveMonacoEditor.set_value(socket, new_history_selection, to: "query")
        |> assign(:query, new_history_selection)
        |> assign(:prev_form, Ecto.Changeset.apply_changes(changeset))
      else
        socket
      end
    {:noreply, assign(socket, :form, to_form(changeset))}
  end

  @impl true
  def handle_event("set_query_value", %{"value" => query}, socket) do
    # do something with `value` - it contains the whole editor content
    Logger.info "ReadOnlyLive: set query to #{query}"
    {:noreply, assign(socket, :query, query)}
  end

  @impl true
  def handle_event("exec_query", %{"read_only_live"=>%{"history"=>_history}}, socket) do
    schema = socket.assigns[:schema]
    query = socket.assigns[:query]
    Logger.info "ReadOnlyLive: query for #{socket.assigns.email}: #{query}"

    query = String.trim(query)
    try do
      with {:ok, result} <- exec_query(query, schema),
           %MyXQL.Result{rows: rows, columns: cols} <- result,
           {:ok, _} <- insert_query_execution(query, schema, socket.assigns.email, length(rows)) do

        {:noreply, assign(socket, rows: prepare_rows(rows), cols: cols)}
      else
        {:error, e} -> Logger.warning("ERROR: #{inspect e}")
          socket = show_error(socket, e)
          {:noreply, assign(socket, err: e, rows: [], cols: [])}
      end
    rescue
      e in MyXQL.Error ->
        Logger.warning("ERROR: #{inspect e}")
        socket = show_error(socket, e)
        {:noreply, assign(socket, err: e, rows: [], cols: [])}
    end
  end

  def show_error(socket, %DBConnection.ConnectionError{message: msg}) do
    put_flash(socket, :error, msg)
  end
  def show_error(socket, %MyXQL.Error{message: msg}) do
    put_flash(socket, :error, msg)
  end
  def show_error(socket, e) do
    put_flash(socket, :error, e)
  end

  @doc ~S"""
  redact any secret values, but also handle any lists/maps that
  may have arisen from columns defined to be JSON
  """
  def prepare_rows(rows) do
    rows
    |> Enum.map(fn row -> for col <- row do
        prepare_value(col)
      end
    end)
  end

  def as_base2_integer_string(bitstr) do
    binary_rep = for(<<x::size(1) <- bitstr>>, do: "#{x}")
                 |> Enum.chunk_every(8)
                 |> Enum.join(" ")
    "0b" <> binary_rep
  end

  @redacting_aes_regex ~r/(AES_(EN|DE)CRYPT\s*)\([^\)]+,[^\)]+\)/i
  @redacting_password_regex ~r/(PASSWORD\s*)\([^\)]+\)/i
  defp prepare_value(val) when is_binary(val) do
    inter = Regex.replace(@redacting_aes_regex, val, "\\1(REDACTED,REDACTED)")
    Regex.replace(@redacting_password_regex, inter, "\\1(REDACTED)")
  end
  defp prepare_value(val) when is_bitstring(val), do: as_base2_integer_string(val)
  defp prepare_value(val) when is_list(val), do: Jason.encode!(val)
  defp prepare_value(val) when is_map(val), do: Jason.encode!(val)
  defp prepare_value(val), do: val

  def exec_query(q, schema) do
    {endpoint, _write_end, schema} = DbPortal.DbMetadata.endpoints_and_schema(schema)
    DbPortal.MonitoredDbRepo.exec_ro_query(q, endpoint, schema)
  end

  @impl true
  def handle_info({:updated_env, %{env: env, schema: schema}}, socket) do
    socket =
      socket
      |> assign(:env, env)
      |> assign(:schema, schema)
      |> update_history_list()
    {:noreply, socket}
  end

  @impl true
  def handle_info({:updated_schema, %{schema: schema}}, socket) do
    socket =
      socket
      |> assign(:schema, schema)
      |> update_history_list()
    {:noreply, socket}
  end

  defmodule ReadOnlyQueryExecution do
    use Ecto.Schema
    import Ecto.Changeset

    schema "readonly_query_executions" do
      field :user, :string
      field :query, :string
      field :rows_returned, :integer # null if error

      belongs_to :db_schema, DbPortal.DbMetadata.DbSchema
      timestamps()
    end

    @required_fields ~w(user query db_schema_id rows_returned)a

    def new(attrs) do
      IO.inspect attrs
      %ReadOnlyQueryExecution{}
      |> cast(attrs, @required_fields)
      |> validate_required(@required_fields)
      |> assoc_constraint(:db_schema)
    end
  end

  def insert_query_execution(query, schema, email, row_count) do
    ReadOnlyQueryExecution.new(%{user: email,
      query: String.trim(query), rows_returned: row_count, db_schema_id: schema})
      |> DbPortal.Repo.insert
  end

  def generate_download(cols, rows) do
    prefix = "data:text/csv;charset=utf-8,"

    rows = for r <- rows,
      do: Enum.map(r, &maybe_replace_pound(&1))

    content =
      [cols | rows]
      |> CSV.dump_to_iodata()

    [prefix | content]
  end

  def maybe_replace_pound(v) when is_binary(v),
    do: String.replace(v, "#", "%23")
  def maybe_replace_pound(v), do: v

  def update_history_list(socket) do
    email = socket.assigns.email
    schema_id = socket.assigns.schema
    list = get_query_history_list(email, schema_id)

    assign(socket, :history_list, DbPortal.Repo.all(list))
  end

  def get_query_history_list(email, schema_id) do
    Ecto.Query.from roq in ReadOnlyQueryExecution,
      select: {roq.query, roq.query },
      where: roq.user == ^email,
      where: roq.db_schema_id == ^schema_id,
      group_by: [roq.query],
      limit: 15,
      order_by: [desc: max(roq.id) ]
  end

end
