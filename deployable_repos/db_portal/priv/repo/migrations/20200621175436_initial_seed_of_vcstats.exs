defmodule DbPortal.Repo.Migrations.InitialSeedOfVcstats do
  use Ecto.Migration
  alias DbPortal.VividCortex.VcStat

  def up() do
    db_values = VcStat.stats
                |> Enum.map(fn [id, stat, desc] ->
                  %{id: id, stat: stat, description: desc}
                end)
    DbPortal.Repo.insert_all "vc_stats", db_values
  end

  def down() do
    ids = VcStat.stats |> Enum.map(fn [id, _stat, _desc]->id end)
    for id <- ids, do: DbPortal.Repo.delete "vc_stats", id
  end
end
