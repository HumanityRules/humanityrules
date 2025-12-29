defmodule DbPortal.Permission do
  use Ecto.Schema
  import Ecto.Changeset
  import Ecto.Query


  schema "permissions" do
    field :email, :string
    field :page, :string
    field :deleted, :boolean, default: false
    field :tickets, :string
    #    field :schema_name, :string, virtual: true

    belongs_to :schema, DbPortal.DbMetadata.DbSchema
    timestamps()
  end

  def create_changeset(permission, attrs) do
    IO.inspect({permission, attrs}, label: "Permission create")
    permission
    |> cast(attrs, [:email, :page, :deleted, :tickets, :schema_id])
    |> validate_format(:email, ~r/@/)
    |> validate_format(:tickets, ~r/^([A-Z]{1,6}-[0-9]{1,6})(,\s*[A-Z]{1,6}-[0-9]{1,6})*$/)
    |> unique_constraint([:email, :page, :schema_id], message: "has already been permissioned for this page and schema")
    |> foreign_key_constraint(:schema_id)
  end

  def update_changeset(permission, attrs) do
    IO.inspect({permission, attrs}, label: "Permission update")
    permission
    |> cast(attrs, [:email, :page, :deleted, :tickets, :schema_id])
    |> validate_required([:email, :page, :tickets, :schema_id])
    |> validate_format(:email, ~r/@/)
    |> validate_format(:tickets, ~r/^([A-Z]{1,6}-[0-9]{1,6})(,\s*[A-Z]{1,6}-[0-9]{1,6})*$/)
    |> unique_constraint([:email, :page, :schema_id], message: "has already been permissioned for this page and schema")
  end

  def permitted_schemas_query(email, view) when is_atom(view) do
    view_str = view |> Macro.underscore() |> String.split("/") |> List.last() |> String.replace_suffix("_live","")
    from p in __MODULE__, where: [deleted: false, email: ^email, page: ^view_str]
  end
  def permitted_schemas_query(email, view_str) when is_binary(view_str) do
    from p in __MODULE__, where: [deleted: false, email: ^email, page: ^view_str]
  end
end

defmodule DbPortal.PermissionAdmin do
  alias DbPortal.Permission
  import Ecto.Query

  #  example query setting a virtual field(schema_name)
  # def custom_index_query(_conn, _schema, query) do
  #   from(p in DbPortal.Permission,
  #     join: s in assoc(p, :schema),
  #     where: p.deleted==false,
  #     select: %Permission{id: p.id, email: p.email, page: p.page, schema_name: s.name }
  #   )
  # end
  def custom_index_query(_conn, _schema, query) do
    from(p in query,
      where: p.deleted==false,
      preload: [schema: :cluster]
    )
  end

  def index(_) do
    [
      email: nil,
      page: nil,
      schema_name: %{value: fn
        %{schema: %{name: schema_name, cluster: %{name: cluster_name}}} -> "#{cluster_name}.#{schema_name}"
        _ -> nil
      end
      },
      tickets: nil
    ]
  end

  def custom_show_query(_conn, _schema, query) do
    IO.puts("**************CUSTOM_SHOW_QUERY &&&&&&&&&&&&&&&&&&&&&")
    from(r in query, preload: [:schema])
  end

  def form_fields(_schema) do
    schemas =   DbPortal.Repo.all(
      from s in DbPortal.DbMetadata.DbSchema,
      preload: [:cluster])
      |> Enum.reject(fn s -> s.cluster.disabled end)
      |> Enum.map(fn sch ->
        fname = "#{sch.cluster.name}.#{sch.name}"
        {fname, sch.id}
      end)
    |> Enum.sort()
    [
      email: %{label: "User email"},
      page: %{choices: [{"Ptosc", "ptosc"}, {"AthenaTools", "athena_tools"}]},
      #      deleted: %{},
      tickets: %{type: :string},
      schema_id: %{choices: schemas}
    ]
  end

  def create_changeset(schema, attrs) do
    IO.puts("PermissionAdmin: create_changeset &#&#&#&#&#&#&#&#&#&#&#&#&#")
    Permission.create_changeset(schema, attrs)
  end
  def update_changeset(schema, attrs) do
    IO.puts("PermissionAdmin: update_changeset &#&#&#&#&#&#&#&#&#&#&#&#&#")
    Permission.update_changeset(schema, attrs)
  end

end
