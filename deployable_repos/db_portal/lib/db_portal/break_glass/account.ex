defmodule DbPortal.BreakGlass.Account do
  use Ecto.Schema
  import Ecto.Changeset
  import Ecto.Query, only: [from: 2]

  require Logger
  alias DbPortal.BreakGlass.Account

  schema "break_glass_accounts" do
    field :user_email, :string
    field :db_user_name, :string
    field :schema_name, :string
    field :cluster_endpoint, :string
    field :requested_at, :utc_datetime_usec
    field :expires_at, :utc_datetime_usec
    field :removed_at, :utc_datetime_usec, default: nil
  end

  @required_fields ~w(user_email db_user_name schema_name cluster_endpoint requested_at expires_at)a

  def new(attrs) do
    %DbPortal.BreakGlass.Account{}
    |> cast(attrs, @required_fields)
    |> validate_required(@required_fields)
  end

  def break_glass(cluster_endpoint, schema_name, user_email) do
    password = create_random_password()

    requested_at_utc = DateTime.utc_now()
    expires_at_utc = DateTime.add(requested_at_utc, 24, :hour)

    with user_name <- db_user_name_from_email(user_email),
         :ok <-
           DbPortal.MonitoredDbRepo.provision_db_user(
             cluster_endpoint,
             schema_name,
             user_name,
             password
           ),
         :ok <-
           insert_audit_record(
             cluster_endpoint,
             schema_name,
             user_name,
             user_email,
             requested_at_utc,
             expires_at_utc
           ) do
      {:ok, user_name, password}
    else
      e -> e
    end
  end

  def scrub_expired_accounts() do
    accts_to_scrub()
    |> Enum.each(&scrub_account/1)
  end

  def accts_to_scrub() do
    DbPortal.Repo.all(
      from a in Account,
        where: a.expires_at <= ^DateTime.utc_now(),
        where: is_nil(a.removed_at)
    )
  end

  defp create_random_password() do
    Ecto.UUID.generate()
  end

  defp db_user_name_from_email(user_email) do
    [user_name, _domain] = String.split(user_email, "@")
    "#{user_name}_breakglass"
  end

  defp scrub_account(acct) do
    IO.inspect(acct, label: "account to scrub")

    case DbPortal.MonitoredDbRepo.delete_database_user(
           acct.db_user_name,
           acct.cluster_endpoint,
           acct.schema_name
         ) do
      :ok -> mark_account_removed(acct)
      {:error, e} -> Logger.error("Failed to drop user for #{inspect(acct)}: #{e}")
    end
  end

  defp insert_audit_record(
         cluster_endpoint,
         schema_name,
         user_name,
         user_email,
         requested_at_utc,
         expires_at_utc
       ) do
    new(%{
      user_email: user_email,
      db_user_name: user_name,
      schema_name: schema_name,
      cluster_endpoint: cluster_endpoint,
      requested_at: requested_at_utc,
      expires_at: expires_at_utc
    })
    |> DbPortal.Repo.insert()

    :ok
  end

  # mark all rows removed for this db_user_name and cluster_endpoint,
  # as when the user is removed it accounts for any/all of these.
  defp mark_account_removed(acct) do
    DbPortal.Repo.update_all(
      from(a in Account,
        where: a.db_user_name == ^acct.db_user_name,
        where: a.cluster_endpoint == ^acct.cluster_endpoint,
        where: is_nil(a.removed_at)
      ),
      set: [removed_at: DateTime.utc_now()]
    )
  end
end
