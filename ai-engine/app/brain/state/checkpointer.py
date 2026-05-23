"""
Checkpointer - State persistence for graph execution
"""
import logging
import json
import uuid
from typing import Dict, Any, Optional
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class Checkpointer:
    """
    Handles state persistence for graph execution
    """
    
    def __init__(self, checkpoint_dir: Optional[str] = None):
        """
        Initialize Checkpointer
        
        Args:
            checkpoint_dir: Directory to store checkpoints (default: ./checkpoints)
        """
        if checkpoint_dir is None:
            checkpoint_dir = "./checkpoints"
        
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Checkpointer initialized with directory: {self.checkpoint_dir}")
    
    def _get_checkpoint_path(self, session_id: str) -> Path:
        """
        Get checkpoint file path for session
        
        Args:
            session_id: Session identifier
            
        Returns:
            Path: Checkpoint file path
        """
        return self.checkpoint_dir / f"{session_id}.json"
    
    def save_checkpoint(
        self,
        session_id: str,
        state: Dict[str, Any],
        node_name: str,
    ) -> bool:
        """
        Save checkpoint state
        
        Args:
            session_id: Session identifier
            state: Current graph state
            node_name: Name of the node being executed
            
        Returns:
            bool: True if successful
        """
        try:
            checkpoint_path = self._get_checkpoint_path(session_id)
            
            checkpoint_data = {
                "session_id": session_id,
                "node_name": node_name,
                "state": state,
                "timestamp": datetime.now().isoformat(),
            }
            
            with open(checkpoint_path, 'w') as f:
                json.dump(checkpoint_data, f, indent=2, default=str)
            
            logger.info(f"Checkpoint saved for session {session_id} at node {node_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save checkpoint: {str(e)}")
            return False
    
    def load_checkpoint(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Load checkpoint state
        
        Args:
            session_id: Session identifier
            
        Returns:
            Dict or None: Checkpoint data if exists
        """
        try:
            checkpoint_path = self._get_checkpoint_path(session_id)
            
            if not checkpoint_path.exists():
                logger.warning(f"Checkpoint not found for session {session_id}")
                return None
            
            with open(checkpoint_path, 'r') as f:
                checkpoint_data = json.load(f)
            
            logger.info(f"Checkpoint loaded for session {session_id}")
            return checkpoint_data
            
        except Exception as e:
            logger.error(f"Failed to load checkpoint: {str(e)}")
            return None
    
    def clear_checkpoint(self, session_id: str) -> bool:
        """
        Clear checkpoint for session
        
        Args:
            session_id: Session identifier
            
        Returns:
            bool: True if successful
        """
        try:
            checkpoint_path = self._get_checkpoint_path(session_id)
            
            if checkpoint_path.exists():
                checkpoint_path.unlink()
                logger.info(f"Checkpoint cleared for session {session_id}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to clear checkpoint: {str(e)}")
            return False
    
    def list_checkpoints(self) -> list:
        """
        List all checkpoints
        
        Returns:
            list: List of checkpoint metadata
        """
        try:
            checkpoints = []
            
            for checkpoint_file in self.checkpoint_dir.glob("*.json"):
                try:
                    with open(checkpoint_file, 'r') as f:
                        data = json.load(f)
                    
                    checkpoints.append({
                        "session_id": data.get("session_id"),
                        "node_name": data.get("node_name"),
                        "timestamp": data.get("timestamp"),
                        "file": checkpoint_file.name,
                    })
                except Exception as e:
                    logger.error(f"Failed to read checkpoint {checkpoint_file}: {str(e)}")
            
            # Sort by timestamp (newest first)
            checkpoints.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
            
            return checkpoints
            
        except Exception as e:
            logger.error(f"Failed to list checkpoints: {str(e)}")
            return []
    
    def clear_old_checkpoints(self, max_age_hours: int = 24) -> int:
        """
        Clear checkpoints older than specified age
        
        Args:
            max_age_hours: Maximum age in hours
            
        Returns:
            int: Number of checkpoints cleared
        """
        try:
            cleared = 0
            now = datetime.now()
            
            for checkpoint_file in self.checkpoint_dir.glob("*.json"):
                try:
                    file_time = datetime.fromtimestamp(checkpoint_file.stat().st_mtime)
                    age_hours = (now - file_time).total_seconds() / 3600
                    
                    if age_hours > max_age_hours:
                        checkpoint_file.unlink()
                        cleared += 1
                        logger.info(f"Cleared old checkpoint: {checkpoint_file.name}")
                except Exception as e:
                    logger.error(f"Failed to process checkpoint {checkpoint_file}: {str(e)}")
            
            logger.info(f"Cleared {cleared} old checkpoints")
            return cleared
            
        except Exception as e:
            logger.error(f"Failed to clear old checkpoints: {str(e)}")
            return 0


# Singleton instance
checkpointer = Checkpointer()
