import compiler.ast
import compiler.consts

import rope.base
from rope.base import pyobjects, pynames, evaluate, builtins


class StaticObjectInference(object):

    def __init__(self, pycore):
        self.pycore = pycore

    def infer_returned_object(self, pyobject, args):
        if args:
            # HACK: Setting parameter objects manually
            # This is not thread safe and might cause problems if `args`
            # does not come from a good call site
            pyobject.get_scope().invalidate_data()
            pyobject._set_parameter_pyobjects(
                args.get_arguments(pyobject.get_param_names(special_args=False)))
        scope = pyobject.get_scope()
        if not scope._get_returned_asts():
            return
        for returned_node in reversed(scope._get_returned_asts()):
            try:
                resulting_pyname = evaluate.get_statement_result(scope,
                                                                 returned_node)
                if resulting_pyname is None:
                    return None
                pyobject = resulting_pyname.get_object()
                if pyobject == pyobjects.get_unknown():
                    return
                if not scope._is_generator():
                    return resulting_pyname.get_object()
                else:
                    return builtins.get_generator(resulting_pyname.get_object())
            except pyobjects.IsBeingInferredError:
                pass

    def infer_parameter_objects(self, pyobject):
        objects = []
        if pyobject.parent is not None and isinstance(pyobject.parent, pyobjects.PyClass):
            if not pyobject.decorators:
                objects.append(pyobjects.PyObject(pyobject.parent))
            elif self._is_staticmethod_decorator(pyobject.decorators.nodes[0]):
                objects.append(pyobjects.get_unknown())
            elif self._is_classmethod_decorator(pyobject.decorators.nodes[0]):
                objects.append(pyobject.parent)
            elif pyobject.get_param_names()[0] == 'self':
                objects.append(pyobjects.PyObject(pyobject.parent))
        params = pyobject.get_param_names(special_args=False)
        for parameter in params[len(objects):]:
            objects.append(pyobjects.get_unknown())
        return objects

    def _is_staticmethod_decorator(self, node):
        return isinstance(node, compiler.ast.Name) and node.name == 'staticmethod'

    def _is_classmethod_decorator(self, node):
        return isinstance(node, compiler.ast.Name) and node.name == 'classmethod'

    def analyze_module(self, pymodule):
        """Analyze `pymodule` for static object inference"""
        visitor = SOIVisitor(self.pycore, pymodule)
        compiler.walk(pymodule.get_ast(), visitor)


class SOIVisitor(object):

    def __init__(self, pycore, pymodule):
        self.pycore = pycore
        self.pymodule = pymodule
        self.scope = pymodule.get_scope()

    def visitCallFunc(self, node):
        for child in node.getChildNodes():
            compiler.walk(child, self)
        scope = self.scope.get_inner_scope_for_line(node.lineno)
        primary, pyname = evaluate.get_primary_and_result(scope, node.node)
        if pyname is None:
            return
        pyfunction = pyname.get_object()
        if not isinstance(pyfunction, pyobjects.PyClass) and \
           '__call__' in pyfunction.get_attributes():
            pyfunction = pyfunction.get_attribute('__call__')
        if isinstance(pyfunction, pyobjects.AbstractFunction):
            args = evaluate.create_arguments(primary, pyfunction, node, scope)
        elif isinstance(pyfunction, pyobjects.PyClass):
            pyclass = pyfunction
            if '__init__' in pyfunction.get_attributes():
                pyfunction = pyfunction.get_attribute('__init__').get_object()
            pyname = pynames.UnboundName(pyobjects.PyObject(pyclass))
            args = evaluate.MixedArguments(pyname, node.args, scope)
        else:
            return
        self._call(pyfunction, args)

    def _call(self, pyfunction, args):
        if isinstance(pyfunction, pyobjects.PyFunction):
            self.pycore.call_info.function_called(
                pyfunction, args.get_arguments(pyfunction.get_param_names()))
        # XXX: Maybe we should not call every builtin function
        if isinstance(pyfunction, builtins.BuiltinFunction):
            pyfunction.get_returned_object(args)

    def visitAssign(self, node):
        for child in node.getChildNodes():
            compiler.walk(child, self)
        visitor = _SOIAssignVisitor()
        nodes = []
        for child in node.nodes:
            compiler.walk(child, visitor)
            nodes.extend(visitor.nodes)
        for assigned, levels in nodes:
            scope = self.scope.get_inner_scope_for_line(node.lineno)
            instance = evaluate.get_statement_result(scope, assigned.expr)
            args_pynames = []
            for ast in assigned.subs:
                args_pynames.append(evaluate.get_statement_result(scope, ast))
            value = self.pycore.object_infer._infer_assignment(
                pynames._Assigned(node.expr, levels), self.pymodule)
            args_pynames.append(pynames.UnboundName(value))
            if instance is not None and value is not None:
                pyobject = instance.get_object()
                if '__setitem__' in pyobject.get_attributes():
                    pyfunction = pyobject.get_attribute('__setitem__').get_object()
                    args = evaluate.ObjectArguments([instance] + args_pynames)
                    self._call(pyfunction, args)


class _SOIAssignVisitor(pyobjects._NodeNameCollector):

    def __init__(self):
        super(_SOIAssignVisitor, self).__init__()
        self.nodes = []

    def _added(self, node, levels):
        if isinstance(node, compiler.ast.Subscript):
            self.nodes.append((node, levels))