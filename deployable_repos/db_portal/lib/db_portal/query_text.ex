defmodule DbPortal.QueryText do
  @moduledoc ~S"""
  Operations on the full query text
  """

  @tag_regex ~r|/\* *(?<key>\w+) *= *(?<value>[a-zA-Z0-9./]+) *\*/|
  @tag_path_regex ~r|/\* PATH -> ([^ ]+)|
  @tag_entrypoint_regex ~r|/\* STACK_\d\d\d->Symfony/src/CourseHero/([^ ]+)|
  @tag_control_regex ~r"/\* STACK_\d\d\d->(Control|Bin)/([^ ]+)"
  @tag_repo_regex ~r|/\* STACK_\d\d\d->Symfony/src/CourseHero/.*/([^/]+)Repository\.php.*|

  @doc ~S'''
  Extract tags from comments within the query text (typically at the end)

  ## Example usage
    iex> DbPortal.QueryText.tags """
    ...> SELECT * FROM foo
    ...> /* tag1=key1 */
    ...> /*    tag2 =   key2*/
    ...> /*    tag_3 =   key2p1.p2.p3*/
    ...> """
    [["tag1", "key1"], ["tag2", "key2"], ["tag_3", "key2p1.p2.p3"]]

    iex> DbPortal.QueryText.tags """
    ...> DELETE
    ...> 				  FROM cfw_db_cache_associations
    ...> 				  WHERE cache_key = '13446731-12'
    ...> 				  AND cache_table = 'related_questions'
    ...> 
    ...> /* framework=symfony */
    ...> /* application=backend */
    ...> /* SYMFONY_COMMAND=coursehero_qa_update-related-questions-cache */
    ...> /...[truncated]
    ...> """
    [["framework", "symfony"], ["application", "backend"]]


    iex> DbPortal.QueryText.tags "/* PATH -> /_lit/Paradise-Lost/documents/ */"
    [["PATH", "/_lit"]]

    iex> DbPortal.QueryText.tags """
    ...> /* STACK_007->Symfony/src/CourseHero/BookBundle/Controller/BookStudyGuideController.php;197;renderRelatedContentSection */
    ...> /* STACK_006->Symfony/src/CourseHero/BookBundle/Controller/BookStudyGuideController.php;66;bookLandingContentAction */
    ...> /* STACK_005->Symfony/vendor/symfony/symfony/src/Symfony/Component/HttpKernel/HttpKernel.php;151;bookLandingContentWrapperAction */
    ...> """
    [["symfony_entrypoint", "BookBundle/Controller/BookStudyGuideController.php;66;bookLandingContentAction"]]

    iex> DbPortal.QueryText.tags "/* STACK_001->/* STACK_001->Control/sf2.php;58;require_once */"
    [["control", "sf2.php;58;require_once"]]
    iex> DbPortal.QueryText.tags "/* STACK_001->/* STACK_001->Bin/cron/foobar.php;58;require_once */"
    [["control", "cron/foobar.php;58;require_once"]]

    iex> # Verify it skips the UtilsBundle captures
    iex> # unless there are no other bundles deeper in the stack
    iex> DbPortal.QueryText.tags """
    ...> /* STACK_011->Symfony/src/CourseHero/ContentServicesBundle/Command/TextAnalysisCommand.php;70;processQueueItem */
    ...> /* STACK_010->Symfony/src/CourseHero/UtilsBundle/Command/AbstractQueueProcessorCommand.php;36;processMessage */
    ...> /* STACK_009->Symfony/src/CourseHero/UtilsBundle/Command/AbstractPerpetualCommand.php;102;singleRun */
    ...> """
    [["symfony_entrypoint", "ContentServicesBundle/Command/TextAnalysisCommand.php;70;processQueueItem"]]
    iex> DbPortal.QueryText.tags """
    ...> /* STACK_010->Symfony/src/CourseHero/UtilsBundle/Command/AbstractQueueProcessorCommand.php;36;processMessage */
    ...> /* STACK_009->Symfony/src/CourseHero/UtilsBundle/Command/AbstractPerpetualCommand.php;102;singleRun */
    ...> """
    [["symfony_entrypoint", "UtilsBundle/Command/AbstractQueueProcessorCommand.php;36;processMessage"]]


    iex> DbPortal.QueryText.tags """
    ...> /* STACK_014->Symfony/vendor/doctrine/orm/lib/Doctrine/ORM/Query/Exec/SingleSelectExecutor.php;50;executeQuery */
    ...> /* STACK_013->Symfony/vendor/doctrine/orm/lib/Doctrine/ORM/Query.php;321;execute */
    ...> /* STACK_012->Symfony/vendor/doctrine/orm/lib/Doctrine/ORM/AbstractQuery.php;962;_doExecute */
    ...> /* STACK_011->Symfony/vendor/doctrine/orm/lib/Doctrine/ORM/AbstractQuery.php;917;executeIgnoreQueryCache */
    ...> /* STACK_010->Symfony/vendor/doctrine/orm/lib/Doctrine/ORM/AbstractQuery.php;720;execute */
    ...> /* STACK_009->Symfony/src/CourseHero/QABundle/Entity/QuestionRepository.php;1767;getResult */
    ...> /* STACK_008->Symfony/src/CourseHero/QABundle/Entity/QuestionRepository.php;1034;getSelectedOpenBasicQuestions */
    ...> /* STACK_007->Symfony/src/CourseHero/ExpertBundle/Service/ExpertQuestionReservationService.php;404;getOpenBasicQuestionsToAnswer */
    ...> """
    [["symfony_entrypoint", "ExpertBundle/Service/ExpertQuestionReservationService.php;404;getOpenBasicQuestionsToAnswer"], ["repo", "Question"]]
  '''

  def tags(query) do
    tags = case Regex.run(@tag_path_regex, query, capture: :all_but_first) do
      nil -> []
      path -> [["PATH", hd(path) |> clean_path]]
    end

    tags = tags ++ case Regex.scan( @tag_entrypoint_regex, query, capture: :all_but_first)
           |> Enum.reverse do

      [] -> []
      reversed_bundles -> # now take the first one that is not UtilsBundle (or else the first one).
        entrypoint = Enum.find(reversed_bundles,
          List.last(reversed_bundles),
          fn [capture] -> !String.starts_with?(capture, "UtilsBundle") end
        )

        [["symfony_entrypoint", hd(entrypoint)]]
    end

    tags = tags ++ case Regex.scan( @tag_control_regex, query, capture: :all_but_first)
           |> List.last do
      nil -> []
      control -> [["control", List.last(control)]]
    end

    tags = tags ++ case Regex.scan( @tag_repo_regex, query, capture: :all_but_first)
           |> List.first do
      nil -> []
      repo -> [["repo", List.last(repo)]]
    end
    tags ++ Regex.scan(@tag_regex, query, capture: :all_but_first)
  end

  @path_cleaners [
      ~r|^/api/v\d/[^/]*|,
      ~r|^/_ssi/file|,
      ~r|^/file|,
      ~r|^/content/rating/document|,
      ~r|^/content/study-guides|,
      ~r|^/doc-asset|,
      ~r|^/document-viewer|,
      ~r|^/lit|,
      ~r|^/manage/tutors|,
      ~r|^/net-assets|,
      ~r|^/premium-qa/authoring|,
      ~r|^/profile|,
      ~r|^/qa/attachment|,
      ~r|^/qa/wait|,
      ~r|^/question-process|,
      ~r|^/scholarships|,
      ~r|^/search/results|,
      ~r|^/sitemap/[^/]*|,
      ~r|^/sg|,
      ~r|^/subjects|,
      ~r|^/tutors-problems|,
      ~r|^/tutors/problems|,
      ~r|^/tutors|,
      ~r|^/unlock-document|,
      ~r|^/unlock-question|,
      ~r|^/u/[^/]*|,
      ~r|^/_lit|
  ]

  def clean_path(p) do
    case Enum.find_value(@path_cleaners, &Regex.run(&1, p)) do
      nil -> p
      [clean_path | _] -> clean_path
    end
  end
end
