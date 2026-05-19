import { Router } from 'express';
import { communityController } from './community.controller';
import { authMiddleware } from '../../middleware/auth';

const router = Router();

// News ingestion (internal AI engine)
// TODO: add API key or IP restriction for security
router.post('/news/ingest', communityController.ingestNews);
router.post('/bot/posts', communityController.createBotPost);

// Feed & Posts
router.get('/posts', communityController.getPosts);
router.post('/posts', authMiddleware, communityController.createPost);
router.get('/posts/:id', communityController.getPost);
router.post('/posts/:id/comments', authMiddleware, communityController.addComment);
router.post('/posts/:id/react', authMiddleware, communityController.toggleReaction);
router.post('/comments/:id/react', authMiddleware, communityController.toggleCommentReaction);

export default router;
