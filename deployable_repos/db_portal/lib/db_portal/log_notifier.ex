defmodule DbPortal.LogNotifier do
  require Logger
  @behaviour :gen_event

  def init(_) do
   if user = Process.whereis(:user) do
      Process.group_leader(self(), user)
      Logger.info("Started #{__MODULE__}")
      {:ok, configure([])}
    else
      {:ok, configure([])}
    end
  end

  def handle_call({:configure, options}, _state) do
    {:ok, :ok, configure(options)}
  end

  def handle_event({:error, _gl, {_, msg, ts, md}}, state) do
    log_event(msg, ts, md, state)
    {:ok, state}
  end

  # catchall
  def handle_event(_event, state) do
    {:ok, state}
  end

  defp log_event(msg, ts, md, {slack_channel, format, metadata, true}) do
    msg = Logger.Formatter.format(format, :error, msg, ts, Keyword.take(md, metadata))
          |> IO.iodata_to_binary
          |> String.slice(0, 4000)
    Slack.Web.Chat.post_message(slack_channel, msg)
  end

  defp log_event(_msg, _ts, _md, {_slack_channel, _format, _metadata, false}), do: nil

  defp configure(options) do
    log_notifier = Keyword.merge(Application.get_env(:logger, :log_notifier, []), options)
    Application.put_env(:logger, :log_notifier, log_notifier)

    slack_channel  = Keyword.get(log_notifier, :slack_channel)
    metadata = Keyword.get(log_notifier, :metadata, [])

    format = Logger.Formatter.compile(Keyword.get(log_notifier, :format))

    enabled = Keyword.get(log_notifier, :env, :dev) == :prod

    {slack_channel, format, metadata, enabled}
  end
end
