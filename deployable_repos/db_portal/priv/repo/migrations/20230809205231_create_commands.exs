defmodule DbPortal.Repo.Migrations.CreateCommands do
  use Ecto.Migration

  def change do
    create table(:commands) do
      add :command, :string
      add :command_output, :string
      add :submitted_by_user_id, references(:users)
      timestamps()
    end
  end
end
