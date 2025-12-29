defmodule DbPortal.Repo.Migrations.InitialSeedOfHosts do
  use Ecto.Migration
  alias DbPortal.VividCortex.Host

  def up() do
    db_values = Host.hosts
                |> Enum.map(fn {name, id}->
                  %{id: id, name: name}
                end)
    DbPortal.Repo.insert_all "vc_hosts", db_values
  end

  def down() do
    ids = Host.stats |> Enum.map(fn {name, id}->id end)
    for id <- ids, do: DbPortal.Repo.delete "vc_hosts", id
  end

end
