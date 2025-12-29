defmodule DbPortal.Repo.Migrations.CreateCommandApprovals do
  use Ecto.Migration

  def change do
    create table(:command_approvals, primary_key: false) do
      add :command_id, references(:commands)
      add :user_id, references(:users)

      timestamps()
    end

    create unique_index(:command_approvals, [:command_id, :user_id])
  end
end
