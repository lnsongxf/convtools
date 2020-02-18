from collections import defaultdict

from .base import *


__all__ = ["GroupBy", "Aggregate", "Reduce", "ReduceFuncs"]


def call_with_params(callable_or_inline_expr, *args, **kwargs):
    if isinstance(callable_or_inline_expr, InlineExpr):
        return callable_or_inline_expr.pass_args(*args, **kwargs)
    elif callable(callable_or_inline_expr):
        return CallFunc(callable_or_inline_expr, *args, **kwargs)
    elif isinstance(callable_or_inline_expr, NaiveConversion) and callable(
        callable_or_inline_expr.value
    ):
        return callable_or_inline_expr.call(*args, **kwargs)

    raise AssertionError("unexpected callable", callable_or_inline_expr)


class BaseReduce(BaseConversion):
    pass


class _BaseReducer:
    def __init__(
        self,
        reduce=None,
        initial_from_first=None,
        expr=None,
        initial=BaseConversion._none,
        default=BaseConversion._none,
        additional_args=None,
        post_conversion=None,
    ):
        self.reduce = reduce
        self.initial_from_first = initial_from_first
        self.expr = expr
        self.initial = initial
        self.default = default
        self.additional_args = additional_args if additional_args else ()
        self.post_conversion = post_conversion

    def configure_parent_reduce_obj(self, reduce_obj):
        if self.expr is not None and reduce_obj.expr is BaseConversion._none:
            reduce_obj.expr = reduce_obj.ensure_conversion(self.expr)
        if (
            self.initial is not BaseConversion._none
            and reduce_obj.initial is BaseConversion._none
        ):
            reduce_obj.initial = self.initial
        if (
            self.default is not BaseConversion._none
            and reduce_obj.default is BaseConversion._none
        ):
            reduce_obj.default = self.default
        if self.additional_args and not reduce_obj.additional_args:
            reduce_obj.additional_args = self.additional_args
        if self.post_conversion:
            reduce_obj.post_conversion = reduce_obj.ensure_conversion(
                self.post_conversion
            )

    def gen_reduce_initial(
        self,
        var_agg_data_value,
        var_row,
        initial,
        expr,
        additional_args,
        ctx,
        indentation_level,
    ):
        raise NotImplementedError

    def gen_reduce_two(
        self,
        var_agg_data_value,
        var_row,
        expr,
        additional_args,
        ctx,
        indentation_level,
    ):
        raise NotImplementedError


class _ReducerExpression(_BaseReducer):
    def __init__(self, *args, **kwargs):
        super(_ReducerExpression, self).__init__(*args, **kwargs)
        if isinstance(self.reduce, str):
            self.reduce = InlineExpr(self.reduce)

    def gen_reduce_initial(
        self,
        var_agg_data_value,
        var_row,
        initial,
        expr,
        additional_args,
        ctx,
        indentation_level,
    ):
        if initial is BaseConversion._none:
            if self.initial_from_first:
                reduce_initial = (
                    call_with_params(
                        self.initial_from_first, expr, *additional_args,
                    )
                    if additional_args
                    else call_with_params(self.initial_from_first, expr)
                )
            else:
                reduce_initial = (
                    Tuple(expr, *additional_args) if additional_args else expr
                )
        else:
            reduce_initial = call_with_params(
                self.reduce, initial, expr, *additional_args,
            )
        return "{indent}{var_agg_data_value} = {code}".format(
            indent=" " * 4 * indentation_level,
            var_agg_data_value=var_agg_data_value,
            code=reduce_initial.gen_code_and_update_ctx(var_row, ctx),
        )

    def gen_reduce_two(
        self,
        var_agg_data_value,
        var_row,
        expr,
        additional_args,
        ctx,
        indentation_level,
    ):
        return "{indent}{var_agg_data_value} = {code}".format(
            indent=" " * 4 * indentation_level,
            var_agg_data_value=var_agg_data_value,
            code=call_with_params(
                self.reduce,
                EscapedString(var_agg_data_value),
                expr,
                *additional_args,
            ).gen_code_and_update_ctx(var_row, ctx),
        )


class _ReducerStatements(_BaseReducer):
    def _format_statements(
        self,
        var_agg_data_value,
        var_row,
        statements,
        indentation_level,
        args,
        ctx,
    ):
        if isinstance(statements, str):
            statements = [statements]
        elif not statements:
            statements = []
        if not statements:
            statements.append("pass")

        code = "\n".join(
            [
                " " * 4 * indentation_level
                + statement % dict(result=var_agg_data_value)
                for statement in statements
            ]
        )
        return code.format(
            *(arg.gen_code_and_update_ctx(var_row, ctx) for arg in args)
        )

    def gen_reduce_initial(
        self,
        var_agg_data_value,
        var_row,
        initial,
        expr,
        additional_args,
        ctx,
        indentation_level,
    ):
        if initial is BaseConversion._none:
            if self.initial_from_first:
                reduce_initial = self._format_statements(
                    var_agg_data_value,
                    var_row,
                    self.initial_from_first,
                    indentation_level,
                    (expr,) + tuple(additional_args),
                    ctx,
                )
            else:
                arg = Tuple(expr, *additional_args) if additional_args else expr
                reduce_initial = self._format_statements(
                    var_agg_data_value,
                    var_row,
                    "%(result)s = {0}",
                    indentation_level,
                    (arg,),
                    ctx,
                )
        else:
            args = (
                initial,
                expr,
                *additional_args,
            )
            reduce_initial = self._format_statements(
                var_agg_data_value,
                var_row,
                self.reduce,
                indentation_level,
                args,
                ctx,
            )
        return reduce_initial

    def gen_reduce_two(
        self,
        var_agg_data_value,
        var_row,
        expr,
        additional_args,
        ctx,
        indentation_level,
    ):
        args = (
            EscapedString(var_agg_data_value),
            expr,
            *additional_args,
        )
        return self._format_statements(
            var_agg_data_value,
            var_row,
            self.reduce,
            indentation_level,
            args,
            ctx,
        )


class _DictReducerStatements(_ReducerStatements):
    def configure_parent_reduce_obj(self, reduce_obj):
        super(_DictReducerStatements, self).configure_parent_reduce_obj(
            reduce_obj
        )
        if reduce_obj.additional_args:
            raise AssertionError("dict agg doesn't support additional_args")
        if isinstance(reduce_obj.expr, (Tuple, List)):
            t = reduce_obj.expr.items
        else:
            raise AssertionError("expr should be tuple/list")
        k, v = reduce_obj.expr.items
        reduce_obj.expr = k
        reduce_obj.additional_args.append(v)


_Sum = _ReducerStatements(
    reduce=["%(result)s += ({1} or 0)",],
    initial_from_first=["%(result)s = ({0} or 0)",],
    default=0,
)
_SumOrNone = _ReducerStatements(
    reduce=[
        "if {1} is None:",
        "    %(result)s = None",
        "elif {0} is not None:",
        "    %(result)s = {0} + {1}",
    ],
    initial_from_first=["%(result)s = {0}"],
    default=None,
)
_Max = _ReducerStatements(
    reduce=["if {1} is not None and {1} > {0}:", "    %(result)s = {1}"],
    initial_from_first=["if {0} is not None:", "    %(result)s = {0}"],
    default=None,
)
_Min = _ReducerStatements(
    reduce=["if {1} is not None and {1} < {0}:", "    %(result)s = {1}"],
    initial_from_first=["if {0} is not None:", "    %(result)s = {0}"],
    default=None,
)
_Count = _ReducerStatements(
    reduce=["%(result)s += 1",],
    initial_from_first=["%(result)s = 1",],
    default=0,
    expr=0,
)
_CountDistinct = _ReducerStatements(
    reduce=["%(result)s.add({1})"],
    initial_from_first=["%(result)s = {{ {0} }}"],
    post_conversion=GetItem().and_(CallFunc(len, GetItem())).or_(0),
    default=0,
)
_First = _ReducerStatements(
    reduce=[], initial_from_first=["%(result)s = {0}"], default=None,
)
_Last = _ReducerStatements(
    reduce=["%(result)s = {1}"],
    initial_from_first=["%(result)s = {0}"],
    default=None,
)
_MaxRow = _ReducerStatements(
    reduce=[
        "if {1} is not None and {0}[0] < {1}:",
        "    %(result)s = ({1}, {2})",
    ],
    initial_from_first=["if {0} is not None:", "    %(result)s = ({0}, {1})",],
    additional_args=(GetItem(),),
    post_conversion=GetItem(1),
    default=None,
)
_MinRow = _ReducerStatements(
    reduce=[
        "if {1} is not None and {0}[0] > {1}:",
        "    %(result)s = ({1}, {2})",
    ],
    initial_from_first=["if {0} is not None:", "    %(result)s = ({0}, {1})",],
    additional_args=(GetItem(),),
    post_conversion=GetItem(1),
    default=None,
)
_Array = _ReducerStatements(
    reduce=["%(result)s.append({1})"],
    initial_from_first=["%(result)s = [{0}]"],
    default=None,
)
_ArrayDistinct = _ReducerStatements(
    reduce=["%(result)s[{1}] = None"],
    initial_from_first=["%(result)s = {{ {0}: None }}"],
    post_conversion=InlineExpr("list({0}.keys())").pass_args(GetItem()),
    default=None,
)


_Dict = _DictReducerStatements(
    reduce=["%(result)s[{1}] = {2}"],
    initial_from_first=["%(result)s = {{ {0}: {1} }}"],
    default=None,
)
_DictArray = _DictReducerStatements(
    reduce=["%(result)s[{1}].append({2})"],
    initial_from_first=[
        "%(result)s = _d = defaultdict(list)",
        "_d[{0}].append({1})",
    ],
    post_conversion=CallFunc(dict, GetItem()),
    default=None,
)
_DictSum = _DictReducerStatements(
    reduce=["%(result)s[{1}] += {2} or 0"],
    initial_from_first=[
        "%(result)s = _d = defaultdict(int)",
        "_d[{0}] += {1} or 0",
    ],
    post_conversion=CallFunc(dict, GetItem()),
    default=None,
)
_DictSumOrNone = _DictReducerStatements(
    reduce=[
        "if {2} is None:",
        "    %(result)s[{1}] = None",
        "elif {0}[{1}] is not None:",
        "    %(result)s[{1}] += {2}",
    ],
    initial_from_first=["%(result)s = _d = defaultdict(int)", "_d[{0}] = {1}"],
    post_conversion=CallFunc(dict, GetItem()),
    default=None,
)
_DictMax = _DictReducerStatements(
    reduce=[
        "if {2} is not None and ({1} not in {0} or {2} > {0}[{1}]):",
        "    %(result)s[{1}] = {2}",
    ],
    initial_from_first=[
        "if {1} is not None:",
        "    %(result)s = {{ {0}: {1} }}",
    ],
    default=None,
)
_DictMin = _DictReducerStatements(
    reduce=[
        "if {2} is not None and ({1} not in {0} or {2} < {0}[{1}]):",
        "    %(result)s[{1}] = {2}",
    ],
    initial_from_first=[
        "if {1} is not None:",
        "    %(result)s = {{ {0}: {1} }}",
    ],
    default=None,
)
_DictCount = _DictReducerStatements(
    reduce=[
        "if {1} not in {0}:",
        "    %(result)s[{1}] = 1",
        "else:",
        "    %(result)s[{1}] += 1",
    ],
    initial_from_first=["%(result)s = {{ {0}: 1 }}"],
    default=None,
)
_DictCountDistinct = _DictReducerStatements(
    reduce=[
        "if {1} not in {0}:",
        "    %(result)s[{1}] = {{ {2} }}",
        "else:",
        "    %(result)s[{1}].add({2})",
    ],
    initial_from_first=["%(result)s = {{ {0}: {{ {1} }} }}"],
    post_conversion=DictComp(
        GetItem(0),
        CallFunc(len, GetItem(1)),
        _predefined_input=GetItem().call_method("items"),
    ),
    default=None,
)
_DictFirst = _DictReducerStatements(
    reduce=["if {1} not in {0}:", "    %(result)s[{1}] = {2}",],
    initial_from_first=["%(result)s = {{ {0}: {1} }}"],
    default=None,
)
_DictLast = _DictReducerStatements(
    reduce=["%(result)s[{1}] = {2}",],
    initial_from_first=["%(result)s = {{ {0}: {1} }}"],
    default=None,
)


class ReduceFuncs:
    """Exposes the list of reduce functions"""

    #: Calculates the sum, skips false values
    Sum = _Sum
    #: Calculates the sum, any ``None`` makes the total sum ``None``
    SumOrNone = _SumOrNone

    #: Finds max value, skips ``None``
    Max = _Max
    #: Finds a row with max value, skips ``None``
    MaxRow = _MaxRow

    #: Finds min value, skips ``None``
    Min = _Min
    #: Finds a row with min value, skips ``None``
    MinRow = _MinRow

    #: Counts objects
    Count = _Count
    #: Counts distinct values
    CountDistinct = _CountDistinct

    #: Stores the first value per group
    First = _First
    #: Stores the last value per group
    Last = _Last

    #: Aggregates values into array
    Array = _Array
    #: Aggregates distinct values into array, preserves order
    ArrayDistinct = _ArrayDistinct

    #: Aggregates values into dict; dict values are last values per group
    Dict = _Dict
    #: Aggregates values into dict; dict values are lists of group values
    DictArray = _DictArray
    #: Aggregates values into dict; dict values are sums of group values,
    #: skipping ``None``
    DictSum = _DictSum
    #: Aggregates values into dict; dict values are sums of group values,
    #: any ``None`` makes the total sum ``None``
    DictSumOrNone = _DictSumOrNone
    #: Aggregates values into dict; dict values are max group values
    DictMax = _DictMax
    #: Aggregates values into dict; dict values are min group values
    DictMin = _DictMin
    #: Aggregates values into dict; dict values are numbers of values in groups
    DictCount = _DictCount
    #: Aggregates values into dict; dict values are numbers of unique values
    #: in groups
    DictCountDistinct = _DictCountDistinct
    #: Aggregates values into dict; dict values are first values per group
    DictFirst = _DictFirst
    #: Aggregates values into dict; dict values are last values per group
    DictLast = _DictLast


reduce_template = """
            if {var_agg_data_value} is _none:
{reduce_initial}
            else:
{reduce_two}

"""
conditional_reduce_template = """
            if {filter_expr}:
                if {var_agg_data_value} is _none:
{reduce_initial}
                else:
{reduce_two}

"""
grouper_template = """
def {converter_name}(data{code_args}):
    _none = {var_none}
    try:
        {var_signature_to_agg_data} = defaultdict(AggData)
        for {var_row} in data:
            {var_agg_data} = {var_signature_to_agg_data}[{code_signature}]

{code_reduce_blocks}

        result = {code_result}
        {code_sorting}
        return result
    except Exception:
        import linecache
        linecache.cache[{converter_name}._fake_filename] = (
            len({converter_name}._code_str),
            None,
            {converter_name}._code_str.splitlines(),
            {converter_name}._fake_filename,
        )
        raise
"""


class Reduce(BaseReduce):
    """Defines the reduce operation to be used during the aggregation"""
    def __init__(
        self,
        to_call_with_2_args,
        expr=BaseReduce._none,
        initial=BaseReduce._none,
        default=BaseReduce._none,
        additional_args=BaseReduce._none,
        **kwargs,
    ):
        """
        Args:
          to_call_with_2_args (one of :py:obj:`ReduceFuncs`, :py:obj:`_ReducerExpression`, :py:obj:`_ReducerStatements`, :py:obj:`callable` of 2 arguments):
            defines the reduce operation.
            `self` can be partially initialized by
            :py:obj:`convtools.aggregations._BaseReducer` via
            `configure_parent_reduce_obj` method call (e.g. for the `Count` reduce
            func the `expr` is not a required argument, so `Count` reduce func
            can partially initialize the `Reduce` operation).
          expr (object): is to be wrapped with :py:obj:`ensure_conversion` and
            used as an object to be reduced
          initial (callable, object): is to be wrapped with :py:obj:`ensure_conversion`
            and used for reducing with the first item. If callable, then the result
            of a call is used.
          default (callable, object): is to be wrapped with :py:obj:`ensure_conversion`
            and used if there was nothing to reduce in a group (e.g. the current
            reduce operation has filtered out some rows, while an adjacent reduce
            operation has got something to reduce). If callable, then the result
            of a call is used.
          additional_args (tuple): each is to be wrapped with :py:obj:`ensure_conversion`
            and passed to the reduce operation along with `expr` as next positional
            arguments
        """
        super(Reduce, self).__init__(kwargs)
        self.expr = expr
        self.initial = initial
        self.default = default
        self.condition = None
        self.post_conversion = None
        self.additional_args = (
            () if additional_args is self._none else additional_args
        )
        if isinstance(to_call_with_2_args, _BaseReducer):
            self.reducer = to_call_with_2_args
        else:
            self.reducer = _ReducerExpression(to_call_with_2_args)

        if self.expr is not self._none:
            self.expr = self.ensure_conversion(self.expr)
        self.additional_args = [
            self.ensure_conversion(arg) for arg in self.additional_args
        ]
        self.reducer.configure_parent_reduce_obj(self)

        if not isinstance(self.expr, BaseConversion):
            raise AssertionError("expr should be instance of BaseConversion")
        if self.initial is self._none and self.default is self._none:
            raise AssertionError(
                "either 'initial' or 'default' is to be provided"
            )
        if self.initial is not self._none:
            self.initial = (
                self.ensure_conversion(self.initial).call()
                if callable(self.initial)
                else self.ensure_conversion(self.initial)
            )
        if self.default is not self._none:
            self.default = self.ensure_conversion(self.default)

    def filter(self, condition_conversion):
        """Defines a conversion to be used as a condition. Only truth values
        will be aggregated.

        Args:
          condition_conversion (object): to be wrapped with
            :py:obj:`ensure_conversion` and used as a condition
        """
        self.condition = condition_conversion
        return self

    def gen_reduce_code_block(self, var_agg_data_value, var_row, ctx):
        if self.condition is None:
            _reduce_template = reduce_template
            indentation_level = 4
        else:
            _reduce_template = conditional_reduce_template
            indentation_level = 5
        reduce_initial = self.reducer.gen_reduce_initial(
            var_agg_data_value,
            var_row,
            self.initial,
            self.expr,
            self.additional_args,
            ctx,
            indentation_level,
        )
        reduce_two = self.reducer.gen_reduce_two(
            var_agg_data_value,
            var_row,
            self.expr,
            self.additional_args,
            ctx,
            indentation_level,
        )
        kwargs = dict(
            var_agg_data_value=var_agg_data_value,
            reduce_initial=reduce_initial,
            reduce_two=reduce_two,
        )

        if self.condition is not None:
            kwargs["filter_expr"] = self.condition.gen_code_and_update_ctx(
                var_row, ctx
            )

        return _reduce_template.format(**kwargs)

    def _gen_code_and_update_ctx(self, code_input, ctx):
        agg_data_item = ctx["_reduce_id_to_var"][id(self)]
        processed_agg_data_item = agg_data_item
        if self.post_conversion:
            processed_agg_data_item = self.post_conversion.gen_code_and_update_ctx(
                agg_data_item, ctx
            )

        if self.default is self._none:
            result = processed_agg_data_item
        else:
            if isinstance(self.default, NaiveConversion) and callable(
                self.default.value
            ):
                default = self.default.call()
            else:
                default = self.default
            var_default = default.gen_code_and_update_ctx("", ctx)
            result = EscapedString(
                f"({var_default} "
                f"if {agg_data_item} is _none "
                f"else {processed_agg_data_item})"
            ).gen_code_and_update_ctx("", ctx)

        return result

    def _depends_on(self, *args):
        super()._depends_on(*args)
        if any(isinstance(dep, BaseReduce) for dep in self.depends_on):
            raise AssertionError("nested aggregation", self.__dict__)


class GroupBy(BaseConversion):
    """Generates the function which aggregates the data, grouping by conversions,
    specified in `__init__` method and returns data in a format defined by
    the parameter passed to `aggregate` method.

    Current optimizations:
     * piping like ``c.group_by(...).aggregate().pipe(...)`` won't run
       the aggregation twice, this is handled as 2 statements
     * using the same reduce clause twice (e.g. one used as an argument
       for some function calls) won't result in calculating this reduce twice
    """
    def __init__(self, *by, **kwargs):
        """Takes any number of conversions to group by

        Args:
          by (tuple): each item is to be wrapped with :py:obj:`ensure_conversion`.
            Each is to resolve to a hashable object to allow using such tuples as
            keys
        """
        self.options = kwargs.pop("_options", {})
        super(GroupBy, self).__init__(self.options)
        self.by = [self.ensure_conversion(_by) for _by in by]
        self.agg_items = None
        self.reducer_result = None
        self.sort_key = False
        self.sort_key_reverse = None

    def prepare_reducer(self, reducer):
        reducer = self.ensure_conversion(reducer)
        if isinstance(reducer, NaiveConversion):
            raise AssertionError("unexpected reducer type", type(reducer))
        return reducer

    def aggregate(self, reducer):
        """Takes the conversion which defines the desired output of aggregation.

        Args:
          reducer (object): to be wrapped with :py:obj:`ensure_conversion`.
            Reducer object should be based on either group by keys
            or `c.reduce(...)` objects
        """
        self_clone = self.clone()
        reducer = self_clone.reducer_result = self.prepare_reducer(reducer)
        reduce_items = []

        if isinstance(reducer, Dict):
            reduce_items = [i for k_v in reducer.key_value_pairs for i in k_v]
        elif isinstance(reducer, (List, Tuple, Set)):
            reduce_items = list(reducer.items)
        elif isinstance(reducer, BaseConversion):
            reduce_items = [reducer]
        else:
            raise AssertionError("unhandled reducer type", type(reducer))
        self_clone.ensure_conversion(reducer)

        agg_items = self_clone.agg_items = []
        for reduce_item in reduce_items:
            if isinstance(reduce_item, BaseReduce):
                agg_items.append(reduce_item)
            else:
                agg_items.extend(
                    dep
                    for dep in reduce_item.depends_on
                    if isinstance(dep, BaseReduce)
                )

        return self_clone

    def filter(self, condition_conv, cast=BaseConversion._none):
        """Same as :py:obj:`convtools.base.BaseComprehensionConversion.filter`.
        The only exception is that it works with results, not initial items."""
        cast = list if cast is self._none else cast
        return super(GroupBy, self).filter(condition_conv, cast=cast)

    def sort(self, key=None, reverse=False):
        """Same as :py:obj:`convtools.base.BaseComprehensionConversion.sort`.
        The only exception is that it works with results, not initial items."""
        self_clone = self.clone()
        self_clone.sort_key = key
        self_clone.sort_key_reverse = reverse
        return self_clone

    def _gen_reducer_result_item(
        self, item, var_signature, var_row, signature_code_items, ctx,
    ):
        code_item = item.gen_code_and_update_ctx(var_row, ctx)
        for code_index, code_signature_item in enumerate(signature_code_items):
            if code_signature_item in code_item:
                signature_item_getter = EscapedString(var_signature)
                if len(signature_code_items) > 1:
                    signature_item_getter = signature_item_getter.item(
                        code_index
                    )
                code_item = code_item.replace(
                    code_signature_item,
                    signature_item_getter.gen_code_and_update_ctx("", ctx),
                )
        return EscapedString(code_item)

    def _rebuild_reducer_result(
        self,
        var_signature_to_agg_data,
        var_signature,
        var_agg_data,
        var_row,
        signature_code_items,
        ctx,
    ):
        if isinstance(self.reducer_result, Dict):
            new_key_value_pairs = []
            for k_v in self.reducer_result.key_value_pairs:
                new_key_value_pairs.append(
                    tuple(
                        self._gen_reducer_result_item(
                            i,
                            var_signature,
                            var_row,
                            signature_code_items,
                            ctx,
                        )
                        for i in k_v
                    )
                )
            code_reducer_result = Dict(
                *new_key_value_pairs
            ).gen_code_and_update_ctx("", ctx)

        elif isinstance(self.reducer_result, BaseCollectionConversion):
            code_reducer_result = self.reducer_result.__class__(
                *(
                    self._gen_reducer_result_item(
                        i, var_signature, var_row, signature_code_items, ctx,
                    )
                    for i in self.reducer_result.items
                )
            ).gen_code_and_update_ctx("", ctx)
        elif isinstance(self.reducer_result, BaseConversion):
            code_reducer_result = self._gen_reducer_result_item(
                self.reducer_result,
                var_signature,
                var_row,
                signature_code_items,
                ctx,
            ).gen_code_and_update_ctx("", ctx)
        else:
            raise AssertionError(
                "unsupported reducer result", self.reducer_result
            )
        return EscapedString(
            f"[{code_reducer_result} "
            f"for {var_signature}, {var_agg_data} "
            f"in {var_signature_to_agg_data}.items()]"
        )

    def _gen_agg_data_container(
        self, number_of_reducers, initial_val=BaseConversion._none
    ):
        attrs = []
        init_lines = []
        for i in range(number_of_reducers):
            attr = "v%d" % i
            attrs.append("'%s'" % attr)
            init_lines.append(f"        self.{attr} = _none")

        agg_data_container_code = (
            "class AggData:\n    __slots__ = [{}]\n    def __init__(self):\n{}"
        ).format(", ".join(attrs), "\n".join(init_lines),)
        ctx = {"_none": initial_val}
        exec(agg_data_container_code, ctx, ctx)
        return ctx["AggData"]

    def _gen_code_and_update_ctx(self, code_input, ctx):
        var_row = "row"
        var_signature = "signature"
        var_signature_to_agg_data = "signature_to_agg_data"
        var_agg_data = "agg_data"
        var_none = "_none"

        signature_code_items = [
            _by.gen_code_and_update_ctx(var_row, ctx) for _by in self.by
        ]
        if len(signature_code_items) == 0:
            code_signature = "True"
        elif len(signature_code_items) == 1:
            code_signature = signature_code_items[0]
        else:
            code_signature = f"({','.join(signature_code_items)},)"

        code_reduce_blocks = []
        code_signature_to_agg_index = {}
        reduce_id_to_var = ctx.setdefault("_reduce_id_to_var", {})
        for agg_index, agg_item in enumerate(self.agg_items):
            var_agg_data_value = (
                EscapedString(var_agg_data)
                .attr(f"v{agg_index}")
                .gen_code_and_update_ctx("", ctx)
            )
            code_reduce_block = agg_item.gen_reduce_code_block(
                var_agg_data_value, var_row, ctx
            )
            code_hash = code_reduce_block.replace(var_agg_data_value, "")

            add_reduce_block = False
            if code_hash in code_signature_to_agg_index:
                reduce_block_index = code_signature_to_agg_index[code_hash]
            else:
                reduce_block_index = len(code_reduce_blocks)
                add_reduce_block = True
                code_signature_to_agg_index[code_hash] = reduce_block_index
            new_var = reduce_id_to_var[id(agg_item)] = (
                EscapedString(var_agg_data)
                .attr(f"v{reduce_block_index}")
                .gen_code_and_update_ctx("", ctx)
            )
            if add_reduce_block:
                code_reduce_blocks.append(
                    code_reduce_block.replace(var_agg_data_value, new_var)
                )

        ctx["defaultdict"] = defaultdict
        ctx["AggData"] = self._gen_agg_data_container(
            len(code_reduce_blocks), self._none
        )

        code_result = self._rebuild_reducer_result(
            var_signature_to_agg_data,
            var_signature,
            var_agg_data,
            var_row,
            signature_code_items,
            ctx,
        ).gen_code_and_update_ctx("", ctx)

        if self.sort_key is not False:
            code_sorting = (
                EscapedString("result")
                .call_method(
                    "sort", key=self.sort_key, reverse=self.sort_key_reverse
                )
                .gen_code_and_update_ctx("", ctx)
            )
        else:
            code_sorting = ""
        code_none_joined_x_times = ",".join(
            [var_none] * len(code_reduce_blocks)
        )

        converter_name = "group_by"
        grouper_code = grouper_template.format(
            code_args=self._get_args_def_code(ctx, as_kwargs=False),
            var_none=NaiveConversion(self._none).gen_code_and_update_ctx(
                "", ctx
            ),
            var_signature_to_agg_data=var_signature_to_agg_data,
            var_row=var_row,
            var_agg_data=var_agg_data,
            converter_name=converter_name,
            code_signature=code_signature,
            code_reduce_blocks="\n".join(code_reduce_blocks),
            code_result=code_result,
            code_sorting=code_sorting,
        )
        group_data_func = self._code_to_converter(
            converter_name=converter_name,
            code=grouper_code,
            ctx=ctx,
            fake_filename="_convtools_gen_group_by"
        )
        return CallFunc(
            group_data_func, GetItem(), *self._get_args_as_func_args()
        ).gen_code_and_update_ctx(code_input, ctx)


def Aggregate(*args, **kwargs):
    """Shortcut for ``GroupBy(True).aggregate(*args, **kwargs)``"""
    return GroupBy(True).aggregate(*args, **kwargs)
