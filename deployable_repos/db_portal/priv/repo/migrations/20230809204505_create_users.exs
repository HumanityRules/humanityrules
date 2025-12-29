defmodule DbPortal.Repo.Migrations.CreateUsers do
  use Ecto.Migration

  def change do
    create table(:users) do
      add :email, :string, unique: true

      timestamps()
    end
  end
end
