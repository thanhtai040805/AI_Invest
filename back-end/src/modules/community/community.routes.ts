import { Router } from 'express';
import { communityController } from './community.controller';
import { authMiddleware } from '../../middleware/auth';
import { internalAuth } from '../../middleware/internalAuth';

const router = Router();

// Writes performed by internal ingestion/AI services.
router.post('/news/ingest', internalAuth, communityController.ingestNews);
router.post('/bot/posts', internalAuth, communityController.createBotPost);

// Feed & Posts
router.get('/posts', communityController.getPosts);
router.post('/posts', authMiddleware, communityController.createPost);
router.get('/posts/:id', communityController.getPost);
router.post('/posts/:id/comments', authMiddleware, communityController.addComment);
router.post('/posts/:id/react', authMiddleware, communityController.toggleReaction);
router.post('/comments/:id/react', authMiddleware, communityController.toggleCommentReaction);
router.get('/insights', communityController.getInsights);
router.get('/experts/top', communityController.getTopExperts);

export default router;
