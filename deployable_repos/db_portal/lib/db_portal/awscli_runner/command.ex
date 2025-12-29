defmodule DbPortal.AwscliRunner.Command do
  use Ecto.Schema
  import Ecto.Changeset

  alias DbPortal.AwscliRunner.User

  schema "commands" do
    field :command, :string
    field :command_output, :string

    belongs_to :submitted_by_user, User
    many_to_many :approved_by_users, User, join_through: "command_approvals"

    timestamps()
  end

  def changeset(struct, params \\ %{}) do
    struct
    |> cast(params, [:command, :submitted_by_user_id])
    |> validate_required([:command, :submitted_by_user_id])
    |> cast_assoc(:approved_by_users, with: &parse_users/1)
  end

  defp parse_users(users) when is_list(users) do
    users
  end
end
