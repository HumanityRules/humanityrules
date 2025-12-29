defmodule DbPortalWeb.AwscliRunnerLive do
  use DbPortalWeb, :live_view
  require Logger

  import Ecto.Changeset
  import Ecto.Query

  alias DbPortal.Repo
  alias DbPortal.AwscliRunner.Command
  alias DbPortal.AwscliRunner.CommandApproval
  alias DbPortal.AwscliRunner.User

  @impl true
  def mount(_params, %{"user_auth" => {email, valid_until, role}} = _session, socket) do
    historical_commands =
      Command
      |> where([c], not is_nil(c.command))
      |> order_by([c], desc: c.inserted_at)
      |> limit(10)
      |> Repo.all()

    changeset = Command.changeset(%Command{})

    is_data_engr = DbPortal.Role.satisfies_need?(:data_engr, {email, valid_until, role})

    {:ok,
     assign(socket,
       historical_commands: historical_commands,
       command_output: nil,
       form: to_form(changeset),
       dry_run_status: nil,
       email: email,
       submitter: nil,
       approved_users: [],
       is_data_engr: is_data_engr
     )}
  end

  @impl true
  def handle_event("loadCommand", %{"command-id" => command_id}, socket) do
    command = Repo.get(Command, command_id)

    if command do
      command_with_users = Repo.preload(command, [:approved_by_users, :submitted_by_user])

      changeset =
        Command.changeset(%Command{}, %{command: command.command, id: command.id})

      {:noreply,
       assign(socket,
         form: to_form(changeset),
         command_output: command.command_output,
         dry_run_status: nil,
         submitter: command_with_users.submitted_by_user.email,
         approved_users: command_with_users.approved_by_users
       )}
    else
      {:noreply, socket}
    end
  end

  @impl true
  def handle_event("updateCommand", %{"command" => command_params}, socket) do
    changeset = Command.changeset(%Command{}, command_params)
    {:noreply, assign(socket, form: to_form(changeset))}
  end

  @impl true
  def handle_event("submitCommand", %{"command" => command_params}, socket) do
    command_params =
      Map.put(
        command_params,
        "submitted_by_user_id",
        get_or_create_user(socket.assigns.email).id
      )

    changeset = Command.changeset(%Command{}, command_params)

    case Repo.insert(changeset) do
      {:ok, _command} ->
        {:noreply,
         assign(socket,
           form: to_form(changeset),
           approved_users: [],
           command_output: "",
           submitter: socket.assigns.email
         )}

      {:error, changeset} ->
        {:noreply, assign(socket, form: to_form(changeset))}
    end
  end

  @impl true
  def handle_event("performDryRun", %{"command" => cmd}, socket) do
    cmd = String.trim(cmd)

    if is_awscli_command?(cmd) do
      cmd = cmd <> " --dryrun"

      {status, out} = start_dryrun(cmd)
      {:noreply, assign(socket, dry_run_status: status, command_output: out)}
    else
      {:noreply,
       assign(socket,
         dry_run_status: :error,
         command_output: "The provided command is not a valid AWS CLI command."
       )}
    end
  end

  @impl true
  def handle_event("approveCommand", _, socket) do
    user = get_or_create_user(socket.assigns.email)
    command_id = socket.assigns.form.params["id"]
    # Create a new CommandApproval record
    approval_attrs = %{
      user_id: user.id,
      command_id: command_id
    }

    Logger.info("Inserting approval #{inspect(approval_attrs)}")

    changeset =
      %CommandApproval{}
      |> CommandApproval.changeset(approval_attrs)
      |> validate_approval(socket.assigns.is_data_engr)

    case DbPortal.Repo.insert(changeset) do
      {:ok, _approval} ->
        approved_users =
          Command
          |> Repo.get!(command_id)
          |> Repo.preload(:approved_by_users)
          |> Map.get(:approved_by_users)

        {:noreply, assign(socket, approved_users: approved_users)}

      {:error, _changeset} ->
        {:noreply, socket}
    end
  end

  defp validate_approval(changeset, is_data_engr) do
    if is_data_engr do
      changeset
    else
      add_error(changeset, :user_id, "Approver must be a data engineer")
    end
  end

  defp get_or_create_user(email) do
    case DbPortal.Repo.get_by(User, email: email) do
      nil ->
        %User{email: email}
        |> User.changeset(%{email: email})
        |> DbPortal.Repo.insert!()

      user ->
        user
    end
  end

  defp is_awscli_command?(cmd) when is_binary(cmd) do
    String.starts_with?(cmd, "aws")
  end

  defp start_dryrun(cmd) when is_binary(cmd) do
    Logger.info("Dry run command: #{cmd}")

    %Porcelain.Result{err: _err, status: status, out: out} =
      Porcelain.shell(cmd, err: :out)

    Logger.info("Dry run completed status == #{status}")

    if status == 0 do
      {:ok, out}
    else
      {:error, out}
    end
  end

end
