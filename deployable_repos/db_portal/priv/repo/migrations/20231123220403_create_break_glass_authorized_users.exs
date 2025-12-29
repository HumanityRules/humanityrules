defmodule DbPortal.Repo.Migrations.CreateBreakGlassAuthorizedUsers do
  use Ecto.Migration

  def change do

    create table("break_glass_authorized_users") do
      add :user_email, :string, size: 100
      timestamps()
    end
  end
end
