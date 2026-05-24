import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Adjust path to import agent_framework from the root directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent_framework import AgentFramework

class TestFreeTierRouting(unittest.TestCase):
    def setUp(self):
        # We will mock the load_local_config to return a clean config dictionary
        self.mock_config = {
            "GEMINI_API_KEY": "PRIMARY_PRO_KEY",
            "GEMINI_FREE_TIER_API_KEY": "SECONDARY_FREE_KEY",
            "MODEL_PRO_1": "gemini-2.5-pro",
            "MODEL_GEMMA": "gemma-4-31b-it",
            "MODEL_FLASH": "gemini-2.5-flash",
        }

    @patch("google.genai.Client")
    def test_client_initialization(self, mock_client_cls):
        # Setup mocks for genai.Client
        mock_client_cls.side_effect = lambda api_key=None: MagicMock(api_key=api_key)

        with patch.object(AgentFramework, "_load_local_config", return_value=self.mock_config):
            framework = AgentFramework()
            
            # Assert both clients were created with their respective keys
            self.assertIsNotNone(framework.client)
            self.assertIsNotNone(framework.free_client)
            self.assertNotEqual(framework.client, framework.free_client)

    @patch("google.genai.Client")
    def test_client_fallback_initialization(self, mock_client_cls):
        # Case where GEMINI_FREE_TIER_API_KEY is missing
        config_no_free = self.mock_config.copy()
        config_no_free.pop("GEMINI_FREE_TIER_API_KEY")

        mock_client_cls.side_effect = lambda api_key=None: MagicMock(api_key=api_key)

        with patch.object(AgentFramework, "_load_local_config", return_value=config_no_free):
            framework = AgentFramework()
            
            # free_client must fallback and point to client directly
            self.assertEqual(framework.client, framework.free_client)

    @patch("google.genai.Client")
    def test_routing_pro_model(self, mock_client_cls):
        # Mock client behavior
        mock_primary = MagicMock()
        mock_free = MagicMock()
        
        # When Client() is called, return our mock clients
        def client_side_effect(api_key=None):
            if api_key == "PRIMARY_PRO_KEY":
                return mock_primary
            elif api_key == "SECONDARY_FREE_KEY":
                return mock_free
            return MagicMock()
            
        mock_client_cls.side_effect = client_side_effect

        with patch.object(AgentFramework, "_load_local_config", return_value=self.mock_config):
            framework = AgentFramework()
            
            # Verify setup
            self.assertEqual(framework.client, mock_primary)
            self.assertEqual(framework.free_client, mock_free)
            
            # Let's mock _get_cloud_models to return gemini-2.5-pro for "PRO" mode
            with patch.object(framework, "_get_cloud_models", return_value=["gemini-2.5-pro"]):
                # Call generate_response_with_fallback
                try:
                    framework.generate_response_with_fallback("hello", "instruction", "PRO")
                except Exception:
                    pass # We just want to check invocation
                
                # Pro model must ONLY invoke generating content on primary/Pro client
                mock_primary.models.generate_content.assert_called_once()
                mock_free.models.generate_content.assert_not_called()

    @patch("google.genai.Client")
    def test_routing_free_tier_model_success(self, mock_client_cls):
        mock_primary = MagicMock()
        mock_free = MagicMock()
        
        mock_client_cls.side_effect = lambda api_key=None: mock_free if api_key == "SECONDARY_FREE_KEY" else mock_primary

        with patch.object(AgentFramework, "_load_local_config", return_value=self.mock_config):
            framework = AgentFramework()
            
            # Let's mock _get_cloud_models to return gemini-2.5-flash for "FLASH" mode
            with patch.object(framework, "_get_cloud_models", return_value=["gemini-2.5-flash"]):
                try:
                    framework.generate_response_with_fallback("hello", "instruction", "FLASH")
                except Exception:
                    pass
                
                # Flash model must invoke generating content on free client FIRST, and NOT fall back if successful
                mock_free.models.generate_content.assert_called_once()
                mock_primary.models.generate_content.assert_not_called()

    @patch("google.genai.Client")
    def test_routing_free_tier_model_fallback(self, mock_client_cls):
        mock_primary = MagicMock()
        mock_free = MagicMock()
        
        # Make the free client raise an exception
        mock_free.models.generate_content.side_effect = Exception("Quota Limit Exceeded or Bad Key")
        
        mock_client_cls.side_effect = lambda api_key=None: mock_free if api_key == "SECONDARY_FREE_KEY" else mock_primary

        with patch.object(AgentFramework, "_load_local_config", return_value=self.mock_config):
            framework = AgentFramework()
            
            # Mock _get_cloud_models to return gemini-2.5-flash
            with patch.object(framework, "_get_cloud_models", return_value=["gemini-2.5-flash"]):
                try:
                    framework.generate_response_with_fallback("hello", "instruction", "FLASH")
                except Exception:
                    pass
                
                # Should attempt free client first, catch exception, and fall back to primary client for the same model
                mock_free.models.generate_content.assert_called_once()
                mock_primary.models.generate_content.assert_called_once()

if __name__ == "__main__":
    unittest.main()
