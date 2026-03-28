from .aituber_persona_node import AITuberPersonaPromptNode

NODE_CLASS_MAPPINGS = {
    "AITuberPersonaPrompt": AITuberPersonaPromptNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AITuberPersonaPrompt": "AITuber Persona Prompt Generator",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
