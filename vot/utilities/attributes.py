
import inspect
from typing import Type
from collections import Iterable, Mapping

from vot import VOTException
from vot.utilities import to_number, to_string, to_logical, singleton, import_class, class_fullname, class_string

class AttributeException(VOTException):
    pass

@singleton
class Undefined():
    pass

def is_undefined(a):
    if a is None:
        return False
    return a == Undefined()

def is_instance_or_subclass(val, class_) -> bool:
    """Return True if ``val`` is either a subclass or instance of ``class_``."""
    try:
        return issubclass(val, class_)
    except TypeError:
        return isinstance(val, class_)

class ReadonlyMapping(Mapping):

    def __init__(self, data):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def __len__(self):
        return len(self._data)

    def __iter__(self):
        return iter(self._data)

class Attribute(object):

    def __init__(self, default=Undefined()):
        self._default = default if is_undefined(default) else self.coerce(default, {})

    def coerce(self, value, _):
        return value

    def dump(self, value):
        return value

    @property
    def default(self):
        return self._default

    @property
    def required(self):
        return is_undefined(self._default)

class Nested(Attribute):

    def __init__(self, acls: Type["Attributee"], **kwargs):
        if not issubclass(acls, Attributee):
            raise AttributeException("Illegal base class {}".format(acls))

        self._acls = acls
        self._required = False

        for aa, afield in getattr(acls, "_declared_attributes", {}).items():
            if afield.required:
                self._required = True

        super().__init__(**kwargs)

    def coerce(self, value, _):
        if value is None:
            return None
        if is_undefined(value):
            return value
        assert isinstance(value, Mapping)
        return self._acls(**value)

    def dump(self, value: "Attributee"):
        if value is None:
            return None
        return value.dump()

    @property
    def required(self):
        return super().required and self._required

    def __getattr__(self, name):
        # This is only here to avoid pylint errors for the actual attribute field
        return super().__getattr__(name)

    def __setattr__(self, name, value):
        # This is only here to avoid pylint errors for the actual attribute field
        super().__setattr__(name, value)

class AttributeeMeta(type):

    @staticmethod
    def _get_fields(attrs: dict, pop=False):
        """Get fields from a class.
        :param attrs: Mapping of class attributes
        """
        fields = []
        for field_name, field_value in attrs.items():
            if is_instance_or_subclass(field_value, Attribute):
                fields.append((field_name, field_value))
        if pop:
            for field_name, _ in fields:
                del attrs[field_name]

        return fields

    # This function allows Schemas to inherit from non-Schema classes and ensures
    #   inheritance according to the MRO
    @staticmethod
    def _get_fields_by_mro(klass):
        """Collect fields from a class, following its method resolution order. The
        class itself is excluded from the search; only its parents are checked. Get
        fields from ``_declared_attributes`` if available, else use ``__dict__``.

        :param type klass: Class whose fields to retrieve
        """
        mro = inspect.getmro(klass)
        # Loop over mro in reverse to maintain correct order of fields
        return sum(
            (
                AttributeeMeta._get_fields(
                    getattr(base, "_declared_attributes", base.__dict__)
                )
                for base in mro[:0:-1]
            ),
            [],
        )


    def __new__(mcs, name, bases, attrs):

        cls_attributes = AttributeeMeta._get_fields(attrs, pop=True)
        klass = super().__new__(mcs, name, bases, attrs)
        inherited_attributes = AttributeeMeta._get_fields_by_mro(klass)

        # Assign attributes on class
        klass._declared_attributes = dict(inherited_attributes + cls_attributes)

        return klass

class Include(Nested):

    def __init__(self, acls: Type["Attributee"]):
        super().__init__(acls)

    def filter(self, **kwargs):
        attributes = getattr(self._acls, "_declared_attributes", {})
        filtered = dict()
        for aname, afield in attributes.items():
            if isinstance(afield, Include):
                filtered.update(afield.filter(**kwargs))
            elif aname in kwargs:
                filtered[aname] = kwargs[aname]
        return filtered

    @property
    def default(self):
        return {}

class Attributee(metaclass=AttributeeMeta):

    def __init__(self, **kwargs):
        attributes = getattr(self.__class__, "_declared_attributes", {})

        unconsumed = set(kwargs.keys())
        unspecified = set(attributes.keys())

        for aname, afield in attributes.items():
            if isinstance(afield, Include):
                iargs = afield.filter(**kwargs)
                super().__setattr__(aname, afield.coerce(iargs, {"parent": self}))
                unconsumed.difference_update(iargs.keys())
                unspecified.difference_update(iargs.keys())
            else:
                if not aname in kwargs:
                    if not afield.required:
                        avalue = afield.default
                    else:
                        continue
                else:
                    avalue = kwargs[aname]
                super().__setattr__(aname, afield.coerce(avalue, {"parent": self}))
            unconsumed.difference_update([aname])
            unspecified.difference_update([aname])

        if unspecified:
            raise AttributeError("Missing arguments: {}".format(", ".join(unspecified)))

        if unconsumed:
            raise AttributeError("Unsupported arguments: {}".format(", ".join(unconsumed)))

    def __setattr__(self, key, value):
        attributes = getattr(self.__class__, "_declared_attributes", {})
        if key in attributes:
            raise AttributeException("Attribute {} is readonly".format(key))
        super().__setattr__(key, value)

    def dump(self):
        attributes = getattr(self.__class__, "_declared_attributes", {})
        if attributes is None:
            return dict()
    
        serialized = dict()
        for aname, afield in attributes.items():
            if isinstance(afield, Include):
                serialized.update(afield.dump(getattr(self, aname, {})))
            else:
                serialized[aname] = afield.dump(getattr(self, aname, afield.default))
                
        return serialized

class Number(Attribute):

    def __init__(self, conversion, val_min=None, val_max=None, **kwargs):
        self._conversion = conversion
        self._val_min = val_min
        self._val_max = val_max
        super().__init__(**kwargs)

    def coerce(self, value, _=None):
        return to_number(value, max_n=self._val_max, min_n=self._val_min, conversion=self._conversion)

class Integer(Number):

    def __init__(self, **kwargs):
        super().__init__(conversion=int, **kwargs)

class Float(Number):

    def __init__(self, **kwargs):
        super().__init__(conversion=float, **kwargs)

class Boolean(Attribute):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def coerce(self, value, _):
        return to_logical(value)

class String(Attribute):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def coerce(self, value, _):
        return to_string(value)

class List(Attribute):

    def __init__(self, contains, separator=",", **kwargs):
        super().__init__(**kwargs)
        assert isinstance(contains, Attribute)
        self._separator = separator
        self._contains = contains

    def coerce(self, value, context=None):
        if isinstance(value, str):
            value = value.split(self._separator)
        if isinstance(value, dict):
            value = value.values()
        if not isinstance(value, Iterable):
            raise AttributeException("Unable to value convert to list")
        if context is None:
            context = dict()
        return [self._contains.coerce(x, dict(key=i, **context)) for i, x in enumerate(value)]

    def __iter__(self):
        # This is only here to avoid pylint errors for the actual attribute field
        raise NotImplementedError

    def __getitem__(self, key):
        # This is only here to avoid pylint errors for the actual attribute field
        raise NotImplementedError

    def __setitem__(self, key, value):
        # This is only here to avoid pylint errors for the actual attribute field
        raise NotImplementedError

    def dump(self, value):
        return [self._contains.dump(x) for x in value]

class Map(Attribute):

    def __init__(self, contains, container=dict, **kwargs):
        super().__init__(**kwargs)
        assert isinstance(contains, Attribute)
        self._contains = contains
        self._container = container

    def coerce(self, value, context=None):
        if not isinstance(value, Mapping):
            raise AttributeException("Unable to value convert to dict")
        container = self._container()
        if context is None:
            context = dict()
        for name, data in value.items():
            container[name] = self._contains.coerce(data, dict(key=name, **context))
        return ReadonlyMapping(container)

    def __iter__(self):
        # This is only here to avoid pylint errors for the actual attribute field
        raise NotImplementedError

    def __getitem__(self, key):
        # This is only here to avoid pylint errors for the actual attribute field
        raise NotImplementedError

    def __setitem__(self, key, value):
        # This is only here to avoid pylint errors for the actual attribute field
        raise NotImplementedError


    def dump(self, value):
        return {k: self._contains.dump(v) for k, v in value.items()}


def default_object_resolver(typename: str, _, **kwargs) -> Attributee:
    """Default object resovler

    Arguments:
        typename {str} -- String representation of a class that can be imported. 
            Should be a subclass of Attributee as it is constructed from kwargs.

    Returns:
        Attributee -- An instance of the class
    """
    clstype = import_class(typename)
    assert issubclass(clstype, Attributee)
    return clstype(**kwargs)

class Object(Attribute):

    def __init__(self, resolver=default_object_resolver, **kwargs):
        super().__init__(**kwargs)
        self._resolver = resolver

    def coerce(self, value, context=None):
        assert isinstance(value, dict)
        class_name = value.get("type", None)
        return self._resolver(class_name, context, **{k: v for k, v in value.items() if not k == "type"})

    def dump(self, value):
        data = value.dump()
        data["type"] = class_fullname(value)
        return data

    def __getattr__(self, name):
        # This is only here to avoid pylint errors for the actual attribute field
        return super().__getattr__(name)

    def __setattr__(self, name, value):
        # This is only here to avoid pylint errors for the actual attribute field
        super().__setattr__(name, value)

class Callable(Attribute):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def coerce(self, value, context=None):
        if callable(value):
            return value

        assert isinstance(value, str)
        caltype = import_class(value)
        assert callable(caltype)
        caltype.resname = value
        return caltype

    def dump(self, value):
        if hasattr(value, "resname"):
            return value.resname
        if inspect.isclass(value):
            return class_string(value)
        return class_fullname(value)

    def __call__(self):
        # This is only here to avoid pylint errors for the actual attribute field
        raise NotImplementedError
