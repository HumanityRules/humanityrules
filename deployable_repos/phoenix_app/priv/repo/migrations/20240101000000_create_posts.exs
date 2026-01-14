defmodule PhoenixApp.Repo.Migrations.CreatePosts do
  use Ecto.Migration

  def change do
    create table(:posts) do
      add :title, :string, null: false
      add :body, :text, null: false

      timestamps(type: :utc_datetime)
    end

    create index(:posts, [:inserted_at])
  end
end
