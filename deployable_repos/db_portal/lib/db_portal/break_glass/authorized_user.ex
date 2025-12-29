defmodule DbPortal.BreakGlass.AuthorizedUser do
  use Ecto.Schema
  import Ecto.Changeset
  import Ecto.Query, only: [from: 2]

  require Logger

  alias DbPortal.BreakGlass.AuthorizedUser

  schema "break_glass_authorized_users" do
    field :user_email, :string
    timestamps()
  end

  @required_fields ~w(user_email)a

  def changeset(entry, attrs) do
    IO.puts("changeset########:  #{inspect entry} #{inspect attrs}")
    entry
    |> cast(attrs, @required_fields)
    |> validate_required(@required_fields)
  end

  def is_authorized?(user_email) do
    nil != DbPortal.Repo.one(from au in AuthorizedUser, where: au.user_email == ^user_email)
  end

end

defmodule DbPortal.BreakGlass.AuthorizedUserAdmin do
end
