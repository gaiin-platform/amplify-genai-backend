FEATURE_FLAGS = {
    "promptOptimizer": True,
    "ragEnabled": True,
    "uploadDocuments": True,
    "overrideInvisiblePrompts": False,
    # 'market': False,
    "promptPrefixCreate": False,  
    "outputTransformerCreate": False, 
    "followUpCreate": True, 
    "workflowCreate": False, 
    "rootPromptCreate": True,  
    "pluginsOnInput": True,  # if all plugin features are disables, then this should be disabled. ex. ragEnabled, codeInterpreterEnabled etc.
    "dataSourceSelectorOnInput": True,
    "automation": True,
    "codeInterpreterEnabled": False,
    "dataDisclosure": False,
    "storeCloudConversations": True,
    "apiKeys": True,
    "assistantAdminInterface": False,
    "createAstAdminGroups": False,
    # 'adminInterface': False, Is dynamically added in get_user_feature_flags
    "artifacts": True,
    "mtdCost": False,
    "highlighter": True,
    "mixPanel": False,
    "assistantPathPublishing": False,  # Controls the feature to publish assistants at custom paths
    "websiteUrls": False,
    "accounts": True,
    "cachedDocuments": False,
    "modelPricing": False
}
