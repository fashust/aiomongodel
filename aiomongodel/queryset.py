"""QuerySet classes."""
import functools
import sys
import textwrap


PY_36 = sys.version_info >= (3, 6)


class MotorQuerySet(object):
    """QuerySet based on Motor query syntax."""

    def __init__(self, doc_class, db):
        self.doc_class = doc_class
        self.db = db
        self.default_query = doc_class.meta.default_query
        self.default_sort = doc_class.meta.default_sort
        self.collection = self.doc_class.meta.collection(self.db)

    def _update_query(self, query):
        return (query
                if not self.default_query
                else {'$and': [self.default_query, query]})

    def clone(self):
        qs = type(self)(self.doc_class, self.db)
        qs.default_query = self.default_query
        qs.collection = self.collection
        return qs

    async def create(self, **kwargs):
        return await self.doc_class.create(self.db, **kwargs)

    async def create_indexes(self):
        if self.doc_class.meta.indexes:
            await self.collection.create_indexes(self.doc_class.meta.indexes)

    async def delete_one(self, query, **kwargs):
        res = await self.collection.delete_one(
            self._update_query(query), **kwargs)
        return res.deleted_count if res.acknowledged else None

    async def delete_many(self, query, **kwargs):
        res = await self.collection.delete_many(
            self._update_query(query), **kwargs)
        return res.deleted_count if res.acknowledged else None

    async def replace_one(self, query, *args, **kwargs):
        res = await self.collection.replace_one(
            self._update_query(query), *args, **kwargs)
        return res.modified_count if res.acknowledged else None

    async def update_one(self, query, *args, **kwargs):
        res = await self.collection.update_one(
            self._update_query(query), *args, **kwargs)
        return res.modified_count if res.acknowledged else None

    async def update_many(self, query, *args, **kwargs):
        res = await self.collection.update_many(
            self._update_query(query), *args, **kwargs)
        return res.modified_count if res.acknowledged else None

    async def find_one(self, query={}, *args, **kwargs):
        data = await self.collection.find_one(
            self._update_query(query), *args, **kwargs)
        if data is None:
            # TODO: raise NotFoundError?
            return None

        return self.doc_class.from_son(data)

    async def get(self, _id, *args, **kwargs):
        return await self.find_one(
            {'_id': self.doc_class._id.to_son(_id)}, *args, **kwargs)

    def find(self, query={}, *args, sort=None, **kwargs):
        if not sort and self.default_sort:
            sort = self.default_sort
        return MotorQuerySetCursor(
            self.doc_class,
            self.collection.find(self._update_query(query),
                                 *args, sort=sort, **kwargs))

    async def count(self, query={}, **kwargs):
        return await self.collection.count(
            self._update_query(query), **kwargs)

    def aggregate(self, pipeline, **kwargs):
        if not self.default_query:
            return self.collection.aggregate(pipeline, **kwargs)

        try:
            match = pipeline[0]['$match']
        except KeyError:
            return self.collection.aggregate(
                [{'$match': self.default_query}] + pipeline, **kwargs)
        else:
            pipeline[0]['$match'] = self._update_query(match)
            return self.collection.aggregate(pipeline, **kwargs)

    def with_options(self, **kwargs):
        clone = self.clone()
        clone.collection = self.collection.with_options(**kwargs)
        return clone


class MotorQuerySetCursor(object):
    """Cursor based on motor cursor."""

    DIRECT_TO_MOTOR = {'distinct', 'explain', 'count'}

    def __init__(self, doc_class, cursor):
        self.doc_class = doc_class
        self.cursor = cursor

    def _proxy_to_motor_cursor(self, method, *args, **kwargs):
        getattr(self.cursor, method)(*args, *kwargs)
        return self

    async def to_list(self, length):
        data = await self.cursor.to_list(length)
        return [self.doc_class.from_son(item) for item in data]

    def clone(self):
        return self.__class__(self.doc_class, self.cursor.clone())

    if PY_36:
        # for python >= 3.6 implement __aiter__ as async generator
        exec(textwrap.dedent("""
        async def __aiter__(self):
            return (self.doc_class.from_son(item)
                    async for item in self.cursor)
        """), globals(), locals())
    else:
        # for python < 3.6 implement __aiter__ as async iterator
        exec(textwrap.dedent("""
        def __aiter__(self):
            return self

        async def __anext__(self):
            return self.doc_class.from_son(await self.cursor.__anext__())
        """), globals(), locals())

    def __getattr__(self, name):
        if name in self.DIRECT_TO_MOTOR:
            return getattr(self.cursor, name)

        return functools.partial(self._proxy_to_motor_cursor, name)