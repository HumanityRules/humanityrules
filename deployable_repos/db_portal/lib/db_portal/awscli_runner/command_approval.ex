defmodule DbPortal.AwscliRunner.CommandApproval do
  use Ecto.Schema
  import Ecto.Changeset

  schema "command_approvals" do
    belongs_to :command, DbPortal.AwscliRunner.Command
    belongs_to :user, DbPortal.AwscliRunner.User

    timestamps()
  end

  def changeset(approval, attrs \\ %{}) do
    approval
    |> cast(attrs, [:command_id, :user_id])
    |> validate_required([:command_id, :user_id])
    |> unique_constraint([:command_id, :user_id])
  end
end
