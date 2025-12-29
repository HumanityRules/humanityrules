defmodule DbPortal.VividCortex.VcStats do
  defmacro __using__(_) do
    quote do
      @stats  [
        [0, "time_us", "Total execution time in microseconds per second"],
        [1, "tput", "Total execution count per second"],
        [2, "rows_examined.tput", "Ros examined per second"]
      ]

      @stat_map @stats |> Enum.reduce(%{}, fn [id,stat,_],acc-> Map.put(acc, stat, id) end)

      def stat_to_id(stat), do: Map.get(@stat_map, stat)
    end
  end
end
