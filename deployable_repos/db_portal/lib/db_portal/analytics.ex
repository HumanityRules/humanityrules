defmodule DbPortal.Analytics do
  alias DbPortal.Repo

  @start_at ~N[2020-06-24 04:00:00]

  def get_tag_values(tag_key, selector \\ "value") do
    Repo.analytics_query( ~s"""
      SELECT DISTINCT #{selector}
      FROM tags t
      WHERE `key` = '#{tag_key}'
      ORDER BY 1
    """
    )
    |> Enum.map(&hd/1)
  end

  def get_data(which_chart, args \\ [])
  def get_data("time_us", _args) do
    get_data("application", "", "time_us", 1_000_000)
  end
  def get_data("tput", _args) do
    get_data("application", "", "tput", 1)
  end
  def get_data("rows_examined.tput", _args) do
    get_data("application", "", "rows_examined.tput", 1_000_000)
  end
  def get_data("bundle.time_us", _args) do
    get_data("symfony_entrypoint", "", "time_us", 1_000_000, ~s[LEFT(t.value, INSTR(t.value, 'Bundle')-1)] )
  end
  def get_data("bundle.tput", _args) do
    get_data("symfony_entrypoint", "", "tput", 1, ~s[left(t.value, instr(t.value, "Bundle")-1)] )
  end
  def get_data("drilldown.time_us", args) do
    key = Keyword.get(args, :key)
    value = Keyword.get(args, :value)
    get_data(key, ~s(AND t.value LIKE '#{value}'), "time_us", 1_000_000)
  end
  def get_data("drilldown.tput", args) do
    key = Keyword.get(args, :key)
    value = Keyword.get(args, :value)
    get_data(key, ~s(AND t.value LIKE '#{value}'), "tput", 1)
  end

  def get_data(tag_key, tag_value_clause, stat, divisor, value_selector \\ "t.value") do

    rows = Repo.analytics_query ~s"""
      SELECT qs.hour, #{value_selector} value,
        SUM(dsc.count*qs.value/cnts.total)/#{divisor} ct_val
      FROM vc_query_stats_by_hour qs
      JOIN digest_sample_counts dsc using(digest, hour, host_id)
      JOIN tags t ON t.id = tag_id
      JOIN vc_stats s ON s.id=stat_id
      JOIN tag_counts_per_hour cnts
        ON cnts.digest=dsc.digest AND cnts.hour=dsc.hour AND cnts.key = t.key
      WHERE t.key='#{tag_key}'
      AND s.stat = '#{stat}'
      AND qs.hour >= '#{@start_at}'
      #{tag_value_clause}
      GROUP BY 1, 2
      HAVING ct_val > 0.3
      ORDER BY hour DESC
    """

    process_rows(rows)

  end

  defp process_rows(rows) do
    tag_values = Enum.map(rows, fn([_,v,_]) -> v end) |> MapSet.new

    # Chartkick.line_chart "[
    #   {name: \"Series A\", data: []},
    #   {name: \"Series B\", data: []}
    # ]"
    for v <- tag_values do
      d = for [hr, tag_value, stat_value] <- rows, v==tag_value, do: [hr, stat_value]

      %{name: v, data: d}
    end

  end

# SELECT qs.hour, t.value, SUM(dsc.count*qs.value/cnts.total)/1 ct_val
# FROM vc_query_stats_by_hour qs
# JOIN digest_sample_counts dsc using(digest, hour, host_id)
# JOIN tags t ON t.id = tag_id
# JOIN vc_stats s ON s.id=stat_id
# JOIN (
# SELECT digest, hour, t.key, SUM(dsc.count) total
# FROM digest_sample_counts dsc
# JOIN tags t ON t.id = tag_id
# GROUP BY digest, hour, t.key) cnts 
#   ON cnts.digest=dsc.digest AND cnts.hour=dsc.hour AND cnts.key = t.key
# WHERE t.key='application'
# AND s.stat = 'rows_examined.tput'
# GROUP BY hour, t.value
end
