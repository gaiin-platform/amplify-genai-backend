FEATURE_FLAGS = {
    'promptOptimizer': True,
    'ragEnabled': True,
    'uploadDocuments': True,
    'overrideInvisiblePrompts': False,
    'market': False,
    'promptPrefixCreate': False, # ask jules
    'outputTransformerCreate': False, # ask jules
    'followUpCreate': True, # ask jules
    'workflowCreate': False, # ask jules
    'rootPromptCreate': True, # ask jules
    'pluginsOnInput': True, # if all plugin features are disables, then this should be disabled. ex. ragEnabled, codeInterpreterEnabled etc.
    'dataSourceSelectorOnInput': True,
    'automation': True,
    'codeInterpreterEnabled': False,
    'dataDisclosure': False,
    'storeCloudConversations': False,
    'apiKeys': True,
    'assistantAdminInterface': False,
    'createAstAdminGroups': False,
    # 'adminInterface': False, Is dynamically added in get_user_feature_flags
    'artifacts': True,
    'mtdCost': False,
    'highlighter': True,
    'assistantApis': False,
    'mixPanel': False,
    'integrations': False
  }