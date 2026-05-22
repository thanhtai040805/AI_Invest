import { Request, Response, NextFunction } from 'express';
import prisma from '../../config/database';
import { AuthRequest } from '../../middleware/auth';
import { z } from 'zod';

const IngestNewsSchema = z.array(z.object({
  newsId: z.string(),
  symbol: z.string(),
  title: z.string(),
  url: z.string(),
  content: z.string().optional().nullable(),
  publishDate: z.string(), // ISO string
  friendlyKeyword: z.string().optional().nullable(),
  sentimentLabel: z.string().optional().nullable(),
  sentimentScore: z.number().optional().nullable(),
}));

const CreatePostSchema = z.object({
  content: z.string().min(1).max(5000),
  taggedSymbols: z.array(z.string()).optional().default([]),
});

const AddCommentSchema = z.object({
  content: z.string().min(1).max(2000),
  parentCommentId: z.string().optional(),
});

export const communityController = {
  async ingestNews(req: Request, res: Response, next: NextFunction) {
    try {
      const data = IngestNewsSchema.parse(req.body);
      
      let insertedCount = 0;
      const newlyInserted = [];
 
      for (const item of data) {
        // Check if already exists
        const existing = await prisma.news.findUnique({
          where: { newsId: item.newsId }
        });
 
        if (!existing) {
          const result = await prisma.news.create({
            data: {
              newsId: item.newsId,
              symbol: item.symbol,
              title: item.title,
              url: item.url,
              content: item.content,
              publishDate: new Date(item.publishDate),
              friendlyKeyword: item.friendlyKeyword,
              sentimentLabel: item.sentimentLabel,
              sentimentScore: item.sentimentScore,
            }
          });
          insertedCount++;
          newlyInserted.push(result);
        }
      }

      res.status(200).json({ 
        success: true, 
        message: `Processed ${data.length} items, inserted ${insertedCount}.`, 
        count: data.length,
        insertedCount,
        newlyInserted 
      });
    } catch (error) {
      next(error);
    }
  },

  async getPosts(req: Request, res: Response, next: NextFunction) {
    try {
      const { limit = '20', cursor } = req.query;
      const take = parseInt(limit as string, 10) || 20;
      
      const posts = await prisma.post.findMany({
        take,
        ...(cursor ? { skip: 1, cursor: { id: cursor as string } } : {}),
        orderBy: { createdAt: 'desc' },
        include: {
          author: { select: { id: true, displayName: true } },
          _count: { select: { comments: true, reactions: true } }
        }
      });

      res.json({ posts, nextCursor: posts.length === take ? posts[posts.length - 1].id : null });
    } catch (error) {
      next(error);
    }
  },

  async createBotPost(req: Request, res: Response, next: NextFunction) {
    try {
      // Very simple authorization for internal bot logic
      const authHeader = req.headers.authorization;
      if (authHeader !== 'Bearer AI_BOT_SECRET_KEY') {
        return res.status(401).json({ error: 'Unauthorized bot' });
      }

      const { content, taggedSymbols } = req.body;
      if (!content) {
        return res.status(400).json({ error: 'Content is required' });
      }

      // Ensure the AI-Bot user exists
      let aiBot = await prisma.user.findFirst({ where: { email: 'bot@aiinvest.com' } });
      if (!aiBot) {
        aiBot = await prisma.user.create({
          data: {
            id: 'ai-bot-id-static',
            displayName: 'AI-Bot',
            email: 'bot@aiinvest.com',
            passwordHash: 'none',
          }
        });
      }

      const post = await prisma.post.create({
        data: {
          authorId: aiBot.id,
          content,
          taggedSymbols: taggedSymbols || [],
        },
        include: {
          author: {
            select: { id: true, displayName: true }
          }
        }
      });

      res.status(201).json(post);
    } catch (error) {
      next(error);
    }
  },

  async getPost(req: Request, res: Response, next: NextFunction) {
    try {
      const { id } = req.params;
      const post = await prisma.post.findUnique({
        where: { id },
        include: {
          author: { select: { id: true, displayName: true } },
          comments: {
            include: {
              author: { select: { id: true, displayName: true } },
              _count: { select: { reactions: true } }
            },
            orderBy: { createdAt: 'asc' }
          },
          _count: { select: { reactions: true } }
        }
      });
      if (!post) {
        return res.status(404).json({ error: 'Post not found' });
      }
      res.json(post);
    } catch (error) {
      next(error);
    }
  },

  async createPost(req: AuthRequest, res: Response, next: NextFunction) {
    try {
      const userId = req.userId!;
      const data = CreatePostSchema.parse(req.body);

      const post = await prisma.post.create({
        data: {
          authorId: userId,
          content: data.content,
          taggedSymbols: data.taggedSymbols,
        },
        include: { author: { select: { id: true, displayName: true } } }
      });

      res.status(201).json(post);
    } catch (error) {
      next(error);
    }
  },

  async addComment(req: AuthRequest, res: Response, next: NextFunction) {
    try {
      const userId = req.userId!;
      const { id: postId } = req.params;
      const data = AddCommentSchema.parse(req.body);

      const post = await prisma.post.findUnique({ where: { id: postId } });
      if (!post) return res.status(404).json({ error: 'Post not found' });

      const comment = await prisma.$transaction(async (tx: any) => {
        const newComment = await tx.comment.create({
          data: {
            postId,
            authorId: userId,
            content: data.content,
            parentCommentId: data.parentCommentId,
          },
          include: { author: { select: { id: true, displayName: true } } }
        });

        await tx.post.update({
          where: { id: postId },
          data: { commentsCount: { increment: 1 } }
        });

        return newComment;
      });

      res.status(201).json(comment);
    } catch (error) {
      next(error);
    }
  },

  async toggleReaction(req: AuthRequest, res: Response, next: NextFunction) {
    try {
      const userId = req.userId!;
      const { id: postId } = req.params;

      const post = await prisma.post.findUnique({ where: { id: postId } });
      if (!post) return res.status(404).json({ error: 'Post not found' });

      const existingReaction = await prisma.reaction.findUnique({
        where: { userId_targetId_targetType: { userId, targetId: postId, targetType: 'POST' } }
      });

      await prisma.$transaction(async (tx: any) => {
        if (existingReaction) {
          await tx.reaction.delete({ where: { id: existingReaction.id } });
          await tx.post.update({ where: { id: postId }, data: { likesCount: { decrement: 1 } } });
        } else {
          await tx.reaction.create({
            data: { userId, targetId: postId, targetType: 'POST' }
          });
          await tx.post.update({ where: { id: postId }, data: { likesCount: { increment: 1 } } });
        }
      });

      res.json({ success: true, action: existingReaction ? 'removed' : 'added' });
    } catch (error) {
      next(error);
    }
  },

  async toggleCommentReaction(req: AuthRequest, res: Response, next: NextFunction) {
    try {
      const userId = req.userId!;
      const { id: commentId } = req.params;

      const comment = await prisma.comment.findUnique({ where: { id: commentId } });
      if (!comment) return res.status(404).json({ error: 'Comment not found' });

      const existingReaction = await prisma.reaction.findUnique({
        where: { userId_targetId_targetType: { userId, targetId: commentId, targetType: 'COMMENT' } }
      });

      if (existingReaction) {
        await prisma.reaction.delete({ where: { id: existingReaction.id } });
      } else {
        await prisma.reaction.create({
          data: { userId, targetId: commentId, targetType: 'COMMENT' }
        });
      }

      res.json({ success: true, action: existingReaction ? 'removed' : 'added' });
    } catch (error) {
      next(error);
    }
  },

  async getInsights(req: Request, res: Response, next: NextFunction) {
    try {
      const breadth = await prisma.$queryRaw`
        SELECT 
          COUNT(*) FILTER (WHERE o.close > o.open) as advancers,
          COUNT(*) FILTER (WHERE o.close < o.open) as decliners,
          COUNT(*) FILTER (WHERE o.close = o.open) as unchanged
        FROM ohlcv o
        WHERE o.time >= NOW() - INTERVAL '1 day'
        AND o.time = (SELECT MAX(time) FROM ohlcv o2 WHERE o2.symbol = o.symbol AND o2.time >= NOW() - INTERVAL '1 day')
      ` as any[];

      const topSectors = await prisma.$queryRaw`
        SELECT s.industry, AVG(s.ref_price) as avg_price, COUNT(*) as stock_count
        FROM stocks s
        WHERE s.industry IS NOT NULL
        GROUP BY s.industry
        ORDER BY stock_count DESC
        LIMIT 3
      ` as any[];

      const hotSector = topSectors[0]?.industry || 'Đa ngành';
      const advancers = Number(breadth[0]?.advancers || 0);
      const decliners = Number(breadth[0]?.decliners || 0);
      const total = advancers + decliners;
      const trend = advancers > decliners ? 'TĂNG' : decliners > advancers ? 'GIẢM' : 'ĐI NGANG';
      const volumeChange = total > 0 ? Math.round(((advancers - decliners) / total) * 1000) / 10 : 0;

      const content = advancers > decliners
        ? `Phe mua đang chiếm ưu thế với ${advancers} mã tăng so với ${decliners} mã giảm. Dòng tiền tập trung vào nhóm ${hotSector}.`
        : `Phe bán đang chiếm ưu thế với ${decliners} mã giảm so với ${advancers} mã tăng. Nhà đầu tư thận trọng với nhóm ${hotSector}.`;

      res.json({
        id: 'insight-live',
        title: 'AI Nhận Định Thị Trường',
        content,
        trend,
        hotSector,
        volumeChange,
      });
    } catch (error) {
      next(error);
    }
  },

  async getTopExperts(req: Request, res: Response, next: NextFunction) {
    try {
      const experts = await prisma.$queryRaw`
        SELECT 
          u.id,
          u.display_name as "displayName",
          u.win_rate as "winRate",
          COUNT(DISTINCT r.id) as reaction_count,
          COUNT(DISTINCT p.id) as post_count
        FROM users u
        LEFT JOIN posts p ON p.author_id = u.id
        LEFT JOIN reactions r ON r.target_id = p.id AND r.target_type = 'POST'
        WHERE u.display_name IS NOT NULL
        GROUP BY u.id, u.display_name, u.win_rate
        ORDER BY reaction_count DESC, post_count DESC
        LIMIT 5
      ` as any[];

      const ranked = experts.map((e: any, i: number) => ({
        id: e.id,
        displayName: e.displayName,
        winRate: Number(e.winRate || 0),
        reactionCount: Number(e.reactionCount || 0),
        postCount: Number(e.postCount || 0),
        rank: i === 0 ? 'Elite' : i < 3 ? 'Pro' : 'Member',
      }));

      res.json(ranked);
    } catch (error) {
      next(error);
    }
  }
};
