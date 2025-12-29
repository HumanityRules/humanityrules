defmodule DbPortal.VividCortex.VcStat do
  @stats  [
    [1, "time_us", "Total execution time in microseconds per second"],
    [2, "tput", "Total execution count per second"],
    [3, "rows_examined.tput", "Rows examined per second"]
  ]

  @stat_map @stats |> Enum.reduce(%{}, fn [id,stat,_],acc-> Map.put(acc, stat, id) end)

  def stat_to_id(stat), do: Map.get(@stat_map, stat)
  def stats(), do: @stats
end
